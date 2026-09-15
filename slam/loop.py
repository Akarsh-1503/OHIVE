"""Loop closure: bag-of-words place recognition, Sim(3) verification, pose-graph.

This is the drift story of the whole system, so all four classical stages are
here and none of them is a stub:

1. tf-idf bag of visual words over ORB descriptors with an inverted index,
2. temporal-gap and covisibility-consistency filtering of candidates,
3. Sim(3) RANSAC (Umeyama) between the two keyframes' matched 3D points, which
   recovers the accumulated **scale** drift as well as the pose error,
4. Gauss-Newton pose-graph optimisation on the Sim(3) Lie algebra over a sparse
   normal-equation system, followed by propagation to map points and to the
   non-keyframe poses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import splu

from .config import SlamConfig
from .features import Matcher, proximity_mask
from .geometry import (
    ransac_sim3,
    sim3_adj,
    sim3_compose,
    sim3_exp,
    sim3_inv,
    sim3_log,
    sim3_right_jinv,
)
from .mapping import KeyFrame, Map

Array = np.ndarray
Sim3 = tuple[float, Array, Array]
Vertices = list[Sim3]

VOCAB_PATH = Path(__file__).with_name("vocab.npz")


class Vocabulary:
    """Binary visual vocabulary: k ORB-shaped centroids plus tf-idf weights."""

    def __init__(self, words: Array, idf: Array) -> None:
        self.words = np.ascontiguousarray(words, dtype=np.uint8)
        self.idf = np.asarray(idf, dtype=np.float32)

    @property
    def size(self) -> int:
        return self.words.shape[0]

    @classmethod
    def load(cls, path: Path = VOCAB_PATH, k: int = 1024, seed: int = 0) -> Vocabulary:
        if path.exists():
            data = np.load(path)
            return cls(data["words"], data["idf"])
        return cls.random(k, seed)

    @classmethod
    def random(cls, k: int = 1024, seed: int = 0) -> Vocabulary:
        """Deterministic fallback when no trained vocabulary is shipped.

        Random binary centroids are a locality-sensitive quantiser rather than a
        learned one: place recognition still works, just with a weaker prior.
        """
        rng = np.random.default_rng(seed)
        return cls(rng.integers(0, 256, (k, 32), dtype=np.uint8), np.ones(k, np.float32))

    def encode(self, desc: Array) -> tuple[Array, Array]:
        """Descriptors -> (word ids, L1-normalised tf-idf weights)."""
        if desc.shape[0] == 0:
            return np.zeros(0, np.int32), np.zeros(0, np.float32)
        _, nidx = cv2.batchDistance(
            np.ascontiguousarray(desc), self.words, cv2.CV_32S, normType=cv2.NORM_HAMMING, K=1
        )
        assign = nidx[:, 0]
        counts = np.bincount(assign[assign >= 0], minlength=self.size).astype(np.float32)
        w = counts * self.idf
        nz = np.flatnonzero(w > 0)
        total = float(w[nz].sum())
        if total <= 0:
            return np.zeros(0, np.int32), np.zeros(0, np.float32)
        return nz.astype(np.int32), (w[nz] / total).astype(np.float32)


class BowDatabase:
    """Inverted index over keyframe BoW vectors, scored with the DBoW2 L1 metric.

    For L1-normalised vectors ``1 - 0.5*sum|a-b|`` collapses to
    ``sum_i min(a_i, b_i)``, so the whole database is scored with one gather and
    one bincount over the stored (keyframe, word, weight) triples.
    """

    def __init__(self, vocab: Vocabulary) -> None:
        self.vocab = vocab
        self._rows = np.zeros(0, np.int32)
        self._cols = np.zeros(0, np.int32)
        self._vals = np.zeros(0, np.float32)
        self.n_entries = 0

    def add(self, kf_id: int, words: Array, vals: Array) -> None:
        if words.size == 0:
            return
        self._rows = np.concatenate([self._rows, np.full(words.size, kf_id, np.int32)])
        self._cols = np.concatenate([self._cols, words])
        self._vals = np.concatenate([self._vals, vals])
        self.n_entries = max(self.n_entries, kf_id + 1)

    def score(self, words: Array, vals: Array) -> Array:
        scores = np.zeros(self.n_entries, dtype=np.float32)
        if words.size == 0 or self._rows.size == 0:
            return scores
        q = np.zeros(self.vocab.size, dtype=np.float32)
        q[words] = vals
        m = np.minimum(self._vals, q[self._cols])
        return np.bincount(self._rows, weights=m, minlength=self.n_entries).astype(np.float32)


@dataclass
class LoopClosure:
    from_kf: int
    to_kf: int
    inliers: int
    scale: float
    pre_error_m: float
    post_error_m: float


@dataclass
class PoseGraphEdge:
    i: int
    j: int
    z: tuple[float, Array, Array]     # measured Sim(3) S_ij
    scale_information: float


class LoopCloser:
    def __init__(
        self, cfg: SlamConfig, world: Map, K: Array, matcher: Matcher, vocab: Vocabulary
    ) -> None:
        self.cfg = cfg
        self.world = world
        self.K = K
        self.matcher = matcher
        self.db = BowDatabase(vocab)
        self.closures: list[LoopClosure] = []
        self.candidates_checked = 0
        self._consistency: list[tuple[set[int], int]] = []
        self.last_scale = 1.0
        self.last_closure_kf = -10 ** 9

    # -- place recognition --------------------------------------------------
    def add_keyframe(self, kf: KeyFrame) -> None:
        words, vals = self.db.vocab.encode(kf.desc)
        kf.bow_words, kf.bow_vals = words, vals
        self.db.add(kf.kf_id, words, vals)

    def relocalisation_candidates(self, desc: Array, limit: int = 5) -> list[KeyFrame]:
        words, vals = self.db.vocab.encode(desc)
        scores = self.db.score(words, vals)
        if scores.size == 0:
            return []
        order = np.argsort(-scores)[:limit]
        return [self.world.keyframes[i] for i in order if scores[i] > 0]

    def detect(self, kf: KeyFrame) -> list[int]:
        """Temporal-gap + covisibility-consistency filtered loop candidates."""
        cfg = self.cfg
        if kf.bow_words is None or kf.kf_id < cfg.lc_min_kf_gap:
            return []
        if kf.kf_id - self.last_closure_kf < cfg.lc_cooldown_kf:
            return []       # the previous closure already re-anchored this stretch
        scores = self.db.score(kf.bow_words, kf.bow_vals)
        # The bar is the weakest score among the keyframes we are *already*
        # connected to: anything that does not beat a known neighbour is noise.
        neighbour_scores = [scores[i] for i in kf.covis if i < scores.size]
        bar = max(cfg.lc_min_score, cfg.lc_score_ratio * min(neighbour_scores, default=0.0))

        blocked = set(kf.covis) | {kf.kf_id}
        cutoff = kf.kf_id - cfg.lc_min_kf_gap
        ranked = np.argsort(-scores)
        raw: list[int] = []
        for i in ranked:
            idx = int(i)
            if idx > cutoff or idx in blocked or scores[idx] < bar:
                continue
            raw.append(idx)
            if len(raw) >= cfg.lc_max_candidates * 3:
                break
        if not raw:
            self._consistency = []
            return []

        accepted: list[int] = []
        next_groups: list[tuple[set[int], int]] = []
        for cand in raw:
            group = set(self.world.keyframes[cand].covis) | {cand}
            hits = 0
            for prev_group, count in self._consistency:
                if group & prev_group:
                    hits = max(hits, count + 1)
            next_groups.append((group, hits))
            if hits >= self.cfg.lc_consistency_hits:
                accepted.append(cand)
        self._consistency = next_groups
        return accepted[: cfg.lc_max_candidates]

    # -- geometric verification --------------------------------------------
    def verify(self, kf: KeyFrame, cand_id: int) -> tuple[float, Array, Array, int] | None:
        """Sim(3) RANSAC between the two keyframes' shared 3D structure."""
        cand = self.world.keyframes[cand_id]
        cur_idx = np.flatnonzero(kf.point_ids >= 0)
        # Match against the candidate's *local map*, not just the one keyframe.
        # A single keyframe only owns a slice of the structure around it, and once
        # keyframes are dense that slice is too thin to verify against -- the true
        # closure on the loop clip was failing RANSAC on 25 correspondences while
        # the candidate's neighbourhood offered four times as many.
        cnd_ids = self.world.local_point_ids(cand, self.cfg.lc_candidate_window)
        if cur_idx.size < self.cfg.lc_min_matches or cnd_ids.size < self.cfg.lc_min_matches:
            return None
        ia, ib = self.matcher.mutual_match(
            kf.desc[cur_idx],
            self.world.points.desc[cnd_ids],
            ratio=self.cfg.lc_match_ratio,
            max_distance=self.cfg.lc_match_max_distance,
        )
        if ia.size < self.cfg.lc_min_matches:
            return None
        pts_cur = self.world.points.xyz[kf.point_ids[cur_idx[ia]]]
        pts_cnd = self.world.points.xyz[cnd_ids[ib]]
        # Work in camera coordinates so the estimated similarity is exactly the
        # relative Sim(3) between the two keyframes.
        Xc_cur = pts_cur @ kf.T_cw[:3, :3].T + kf.T_cw[:3, 3]
        Xc_cnd = pts_cnd @ cand.T_cw[:3, :3].T + cand.T_cw[:3, 3]
        # The residual lives in the *current* keyframe's units, and after a loop's
        # worth of scale drift the two keyframes can be an order of magnitude apart,
        # so the threshold has to come from the destination frame.
        depth = float(np.median(np.linalg.norm(Xc_cur, axis=1)))
        thr = max(self.cfg.lc_inlier_ratio_thresh * depth, 1e-6)
        out = ransac_sim3(
            Xc_cnd,
            Xc_cur,
            thr,
            iterations=self.cfg.lc_ransac_iters,
            min_inliers=self.cfg.lc_min_sim3_inliers,
            rng=np.random.default_rng(self.cfg.seed + kf.kf_id),
        )
        if out is None:
            return None
        s, R, t, mask = out
        n_in = int(mask.sum())
        if n_in < self.cfg.lc_min_sim3_inliers:
            return None
        if n_in / max(ia.size, 1) < self.cfg.lc_min_inlier_ratio:
            return None
        if not (0.02 < s < 50.0):
            return None       # an extreme scale jump is a mismatch, not a loop
        n_guided = self._guided_support(kf, cand, s, R, t)
        if n_guided < self.cfg.lc_min_guided_matches:
            return None
        return s, R, t, n_guided

    def _guided_support(
        self, kf: KeyFrame, cand: KeyFrame, s: float, R: Array, t: Array
    ) -> int:
        """Re-match *all* of the candidate's map points through the estimated Sim(3).

        The Sim(3) was fitted to a couple of dozen correspondences, and a Sim(3)
        pose graph can satisfy almost any single loop edge by rescaling, so
        neither the RANSAC inlier count nor the post-optimisation residual proves
        the closure is real. If the two keyframes genuinely see the same place,
        projecting the candidate's whole map through the transform lands a lot
        more points on matching features; a corridor that merely looks similar
        does not. This is the check that stopped a false closure on
        samples/synthetic_corridor.mp4, which by construction never revisits.
        """
        ids = self.world.local_point_ids(cand, self.cfg.lc_candidate_window)
        if ids.size < 10:
            return 0
        pts = self.world.points.xyz[ids]
        Xc_cand = pts @ cand.T_cw[:3, :3].T + cand.T_cw[:3, 3]
        Xc_cur = s * (Xc_cand @ R.T) + t
        z = Xc_cur[:, 2]
        ok = z > 1e-6
        if int(ok.sum()) < 10:
            return 0
        uv = np.empty((int(ok.sum()), 2))
        Xv = Xc_cur[ok]
        uv[:, 0] = self.K[0, 0] * Xv[:, 0] / Xv[:, 2] + self.K[0, 2]
        uv[:, 1] = self.K[1, 1] * Xv[:, 1] / Xv[:, 2] + self.K[1, 2]
        radius = self.cfg.lc_guided_radius_px
        mask = proximity_mask(uv.astype(np.float32), kf.kps, radius)
        ja, _, _ = self.matcher.ratio_match(
            self.world.points.desc[ids[ok]],
            kf.desc,
            mask=mask,
            ratio=0.9,
            max_distance=self.cfg.lc_match_max_distance,
        )
        return int(ja.size)

    # -- pose graph ---------------------------------------------------------
    def close(
        self, kf: KeyFrame, cand_id: int, sim3: tuple[float, Array, Array, int]
    ) -> tuple[LoopClosure, Vertices, Vertices] | None:
        s, R, t, n_inliers = sim3
        world = self.world
        vertices: list[tuple[float, Array, Array]] = []
        for k in world.keyframes:
            T_wc = np.linalg.inv(k.T_cw)
            vertices.append((1.0, T_wc[:3, :3].copy(), T_wc[:3, 3].copy()))
        old_vertices = [(sv, Rv.copy(), tv.copy()) for sv, Rv, tv in vertices]

        edges = self._odometry_edges(vertices)
        # The verification gives S_cur_cand (candidate camera -> current camera);
        # the graph wants S_ij with i = candidate, j = current.
        z_loop = sim3_inv((s, R, t))
        edges.append(PoseGraphEdge(cand_id, kf.kf_id, z_loop, 1.0))

        pre = self._loop_residual_m(vertices, cand_id, kf.kf_id, z_loop)
        span_pre = self._graph_span(vertices)
        # A loop edge demanding a correction comparable to the whole trajectory is
        # not a loop, it is a mismatch between two places that merely look alike.
        if pre > self.cfg.lc_max_correction_ratio * span_pre:
            return None

        vertices = self._optimise(vertices, edges, fixed=0)
        span_post = self._graph_span(vertices)
        post = self._loop_residual_m(vertices, cand_id, kf.kf_id, z_loop)
        # Sim(3) optimisation can rescale the whole map, so express the post-error
        # back in the pre-optimisation units; otherwise the headline reduction is
        # partly just a change of ruler.
        post *= span_pre / max(span_post, 1e-12)

        # Final acceptance: a true closure is consistent with the odometry chain
        # and the residual collapses. A false one fights the chain and settles at
        # a compromise -- measured 32% reduction for the corridor false positive
        # against 96-100% for the real ones. Roll back rather than corrupt the map.
        if pre > 1e-9 and (1.0 - post / pre) < self.cfg.lc_min_pgo_reduction:
            return None

        self._apply(old_vertices, vertices)
        self.last_scale = float(s)
        self.last_closure_kf = kf.kf_id
        closure = LoopClosure(kf.kf_id, cand_id, n_inliers, float(s), pre, post)
        self.closures.append(closure)
        return closure, old_vertices, vertices

    def _odometry_edges(self, vertices: list[tuple[float, Array, Array]]) -> list[PoseGraphEdge]:
        cfg = self.cfg
        edges: list[PoseGraphEdge] = []
        n = len(vertices)
        for i in range(n - 1):
            z = sim3_compose(sim3_inv(vertices[i]), vertices[i + 1])
            edges.append(PoseGraphEdge(i, i + 1, z, cfg.pgo_scale_information))
        # Covisibility edges make the graph rigid so the correction spreads over
        # the whole loop instead of concentrating at the closure.
        for kf in self.world.keyframes:
            for other, shared in kf.covis.items():
                if other <= kf.kf_id + 1 or shared < 80:
                    continue
                z = sim3_compose(sim3_inv(vertices[kf.kf_id]), vertices[other])
                edges.append(PoseGraphEdge(kf.kf_id, other, z, cfg.pgo_scale_information))
        return edges

    def _optimise(
        self,
        vertices: list[tuple[float, Array, Array]],
        edges: list[PoseGraphEdge],
        fixed: int,
    ) -> list[tuple[float, Array, Array]]:
        """Gauss-Newton on sim(3) with right perturbations V <- V * Exp(delta).

        For r = Log(Z^-1 Vi^-1 Vj) the exact Jacobians are
        ``dr/ddelta_i = -Jr^-1(r) Adj(Vj^-1 Vi)`` and ``dr/ddelta_j = Jr^-1(r)``.
        The gauge is fixed by leaving the reference vertex out of the system
        entirely, which keeps the normal matrix non-singular without a prior.
        """
        n = len(vertices)
        dim = 7
        slot = np.full(n, -1, dtype=np.int64)
        free = [i for i in range(n) if i != fixed]
        slot[free] = np.arange(len(free))
        size = len(free) * dim
        if size == 0:
            return vertices
        grid = np.arange(dim)
        lam = 1e-6
        prev_cost = np.inf
        for _ in range(self.cfg.pgo_iterations):
            rows: list[Array] = []
            cols: list[Array] = []
            vals: list[Array] = []
            b = np.zeros(size)
            cost = 0.0
            for e in edges:
                Vi, Vj = vertices[e.i], vertices[e.j]
                r = sim3_log(*sim3_compose(sim3_inv(e.z), sim3_compose(sim3_inv(Vi), Vj)))
                Jr_inv = sim3_right_jinv(r)
                Ji = -Jr_inv @ sim3_adj(sim3_compose(sim3_inv(Vj), Vi))
                omega = np.ones(dim)
                omega[6] = e.scale_information
                nr = float(np.sqrt(r @ (omega * r)))
                # Huber down-weighting keeps one bad edge from bending the graph.
                w = 1.0 if nr <= 1.0 else 1.0 / nr
                cost += w * nr * nr
                wo = omega * w
                blocks = [(slot[e.i], Ji), (slot[e.j], Jr_inv)]
                for ai, Ja in blocks:
                    if ai < 0:
                        continue
                    JtW = Ja.T * wo
                    b[ai * dim : ai * dim + dim] -= JtW @ r
                    for bi, Jb in blocks:
                        if bi < 0:
                            continue
                        rows.append(np.repeat(ai * dim + grid, dim))
                        cols.append(np.tile(bi * dim + grid, dim))
                        vals.append((JtW @ Jb).ravel())
            H = coo_matrix(
                (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                shape=(size, size),
            ).tocsc()
            H.setdiag(H.diagonal() + lam)
            try:
                delta = splu(H).solve(b)
            except (RuntimeError, ValueError):
                break
            if not np.all(np.isfinite(delta)):
                break
            for i, v in enumerate(free):
                vertices[v] = sim3_compose(
                    vertices[v], sim3_exp(delta[i * dim : i * dim + dim])
                )
            if prev_cost - cost < 1e-9 * max(prev_cost, 1.0):
                break
            prev_cost = cost
        return vertices

    @staticmethod
    def _graph_span(vertices: list[tuple[float, Array, Array]]) -> float:
        """Total keyframe-to-keyframe path length; the graph's own length unit."""
        if len(vertices) < 2:
            return 1.0
        c = np.array([v[2] for v in vertices])
        return float(np.linalg.norm(np.diff(c, axis=0), axis=1).sum())

    def _loop_residual_m(
        self,
        vertices: list[tuple[float, Array, Array]],
        i: int,
        j: int,
        z: tuple[float, Array, Array],
    ) -> float:
        """||predicted position of j through the loop edge - current position||."""
        predicted = sim3_compose(vertices[i], z)
        return float(np.linalg.norm(predicted[2] - vertices[j][2]))

    def _apply(
        self,
        old: list[tuple[float, Array, Array]],
        new: list[tuple[float, Array, Array]],
    ) -> None:
        world = self.world
        pts = world.points
        n = pts.n
        if n:
            anchor = pts.first_kf[:n]
            for kf_id in np.unique(anchor):
                sel = np.flatnonzero(anchor == kf_id)
                if sel.size == 0:
                    continue
                s_o, R_o, t_o = old[int(kf_id)]
                s_n, R_n, t_n = new[int(kf_id)]
                # World -> anchor camera with the old pose, back out with the new.
                X_cam = (pts.xyz[sel] - t_o) @ R_o / s_o
                pts.xyz[sel] = s_n * (X_cam @ R_n.T) + t_n
        for kf, (_s_n, R_n, t_n) in zip(world.keyframes, new):
            T_cw = np.eye(4)
            T_cw[:3, :3] = R_n.T
            T_cw[:3, 3] = -R_n.T @ t_n
            kf.T_cw = T_cw
        world.invalidate()

def correct_frame_pose(
    T_cr: Array, old: tuple[float, Array, Array], new: tuple[float, Array, Array]
) -> Array:
    """Re-anchor a non-keyframe pose after its reference keyframe moved.

    ``T_cr`` is camera-from-reference-camera and is scale free, so only the
    reference keyframe's similarity has to be re-applied: the rotation composes
    and the camera centre rides the similarity.
    """
    s_n, R_n, t_n = new
    R_cr, t_cr = T_cr[:3, :3], T_cr[:3, 3]
    c_ref = -R_cr.T @ t_cr
    c_w = s_n * (R_n @ c_ref) + t_n
    R_wc = R_n @ R_cr.T
    T_cw = np.eye(4)
    T_cw[:3, :3] = R_wc.T
    T_cw[:3, 3] = -R_wc.T @ c_w
    return T_cw
