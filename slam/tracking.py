"""Calibration bootstrap, two-view initialisation and frame-to-map tracking."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .config import SlamConfig
from .features import Frame, Matcher, dedupe_by_distance, proximity_mask
from .geometry import (
    parallax_deg,
    project,
    projection_matrix,
    se3_exp,
    se3_inv,
    se3_log,
    triangulate,
)
from .mapping import KeyFrame, Map, refine_pose_pnp

Array = np.ndarray

# Chi-square 95% thresholds for a 2-dof (point) and 1-dof (line) residual.
_TH_H = 5.991
_TH_F = 3.841


def default_intrinsics(width: int, height: int, cfg: SlamConfig) -> Array:
    """Pinhole guess, or the caller's override where they supplied one."""
    fx = cfg.fx if cfg.fx is not None else cfg.default_focal_ratio * width
    fy = cfg.fy if cfg.fy is not None else fx
    cx = cfg.cx if cfg.cx is not None else width / 2.0
    cy = cfg.cy if cfg.cy is not None else height / 2.0
    return np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]])


def _score_homography(H: Array, p1: Array, p2: Array, sigma: float = 1.0) -> float:
    """ORB-SLAM's symmetric transfer score; higher means the model explains more."""
    inv_s2 = 1.0 / (sigma * sigma)
    Hi = np.linalg.inv(H)
    fwd = cv2.perspectiveTransform(p1.reshape(-1, 1, 2), H).reshape(-1, 2)
    bwd = cv2.perspectiveTransform(p2.reshape(-1, 1, 2), Hi).reshape(-1, 2)
    e1 = np.einsum("ij,ij->i", p2 - fwd, p2 - fwd) * inv_s2
    e2 = np.einsum("ij,ij->i", p1 - bwd, p1 - bwd) * inv_s2
    s = np.where(e1 < _TH_H, _TH_H - e1, 0.0) + np.where(e2 < _TH_H, _TH_H - e2, 0.0)
    return float(s.sum())


def _score_fundamental(F: Array, p1: Array, p2: Array, sigma: float = 1.0) -> float:
    inv_s2 = 1.0 / (sigma * sigma)
    h1 = np.concatenate([p1, np.ones((p1.shape[0], 1))], axis=1)
    h2 = np.concatenate([p2, np.ones((p2.shape[0], 1))], axis=1)
    l2 = h1 @ F.T
    l1 = h2 @ F
    num = np.einsum("ij,ij->i", h2, l2) ** 2
    d2 = num / np.maximum(l2[:, 0] ** 2 + l2[:, 1] ** 2, 1e-12) * inv_s2
    d1 = num / np.maximum(l1[:, 0] ** 2 + l1[:, 1] ** 2, 1e-12) * inv_s2
    s = np.where(d2 < _TH_F, _TH_H - d2, 0.0) + np.where(d1 < _TH_F, _TH_H - d1, 0.0)
    return float(s.sum())


def refine_focal(F: Array, width: int, height: int, cfg: SlamConfig) -> float:
    """MAP estimate of the focal length from the two-view fundamental matrix.

    Data term: a true essential matrix has singular values (s, s, 0), so sweeping
    f and measuring the normalised gap |s0-s1|/(s0+s1) of ``K^T F K`` is the
    classic Sturm/Bougnoux self-calibration test -- 13 3x3 SVDs, essentially free.

    Prior term: that data term is close to flat whenever the motion is
    rotation-dominated, which handheld video very often is, and it then biases
    long. A log-normal prior around ``default_focal_ratio * width`` keeps the
    estimate inside the range consumer cameras actually occupy. The focal is
    still an *estimate*; the reconstruction is up to scale either way and a
    residual focal error shows up as a mild global shape distortion.
    """
    cx, cy = width / 2.0, height / 2.0
    f_prior = cfg.default_focal_ratio * width
    best_f = f_prior
    best_score = np.inf
    for ratio in np.linspace(cfg.focal_search_lo, cfg.focal_search_hi, cfg.focal_search_steps):
        f = ratio * width
        K = np.array([[f, 0.0, cx], [0.0, f, cy], [0.0, 0.0, 1.0]])
        s = np.linalg.svd(K.T @ F @ K, compute_uv=False)
        denom = s[0] + s[1]
        if denom < 1e-12:
            continue
        data = abs(s[0] - s[1]) / denom
        score = data + cfg.focal_prior_weight * np.log(f / f_prior) ** 2
        if score < best_score:
            best_score = score
            best_f = f
    return float(best_f)


@dataclass
class InitResult:
    K: Array
    T_cw: Array              # pose of the current frame; the reference frame is the origin
    pts_w: Array             # (n, 3) triangulated points in the reference camera frame
    ref_idx: Array           # keypoint indices in the reference frame
    cur_idx: Array           # keypoint indices in the current frame
    model: str               # "essential" or "homography"
    median_parallax_deg: float
    focal_px: float


def _filter_triangulation(
    K: Array, T_cw: Array, pts_w: Array, p1: Array, p2: Array, cfg: SlamConfig
) -> Array:
    """Cheirality, parallax and reprojection gates on a freshly triangulated set."""
    if pts_w.shape[0] == 0:
        return np.zeros(0, dtype=bool)
    z1 = pts_w[:, 2]
    Xc2 = pts_w @ T_cw[:3, :3].T + T_cw[:3, 3]
    ok = (z1 > 1e-4) & (Xc2[:, 2] > 1e-4) & np.isfinite(pts_w).all(axis=1)
    c2 = -T_cw[:3, :3].T @ T_cw[:3, 3]
    ang = parallax_deg(pts_w, np.zeros(3), c2)
    ok &= ang > cfg.init_min_parallax_deg
    e1 = np.linalg.norm(project(K, np.eye(4), pts_w)[0] - p1, axis=1)
    e2 = np.linalg.norm(project(K, T_cw, pts_w)[0] - p2, axis=1)
    return ok & (e1 < 2.0) & (e2 < 2.0)


class Initializer:
    def __init__(self, cfg: SlamConfig, matcher: Matcher) -> None:
        self.cfg = cfg
        self.matcher = matcher

    def try_init(self, ref: Frame, cur: Frame) -> InitResult | None:
        cfg = self.cfg
        ia, ib = self.matcher.mutual_match(ref.desc, cur.desc)
        if ia.size < cfg.init_min_matches:
            return None
        p1 = ref.kps[ia].astype(np.float64)
        p2 = cur.kps[ib].astype(np.float64)
        # A static or purely rotating camera has no baseline to recover.
        if float(np.median(np.linalg.norm(p2 - p1, axis=1))) < 2.0:
            return None

        h, w = ref.gray.shape[:2]
        if cfg.fx is not None:
            # Explicit calibration override: trust it completely, never search.
            K_user = default_intrinsics(w, h, cfg)
            return self._with_intrinsics(K_user, p1, p2, ia, ib, w, h, None, None, 0.0)
        F, mask_f = cv2.findFundamentalMat(p1, p2, cv2.FM_RANSAC, 1.0, 0.999, 2000)
        if F is None or F.shape != (3, 3):
            return None
        H, mask_h = cv2.findHomography(p1, p2, cv2.RANSAC, 2.5, maxIters=2000)

        s_f = _score_fundamental(F, p1, p2)
        s_h = _score_homography(H, p1, p2) if H is not None else 0.0
        r_h = s_h / max(s_h + s_f, 1e-9)

        focal = (
            refine_focal(F, w, h, cfg)
            if cfg.refine_focal and cfg.fx is None
            else default_intrinsics(w, h, cfg)[0, 0]
        )
        candidates = [focal]
        fallback = cfg.default_focal_ratio * w
        if abs(fallback - focal) > 1.0:
            candidates.append(fallback)

        best: InitResult | None = None
        best_count = 0
        for f in candidates:
            K = np.array([[f, 0.0, w / 2.0], [0.0, f, h / 2.0], [0.0, 0.0, 1.0]])
            res = self._with_intrinsics(K, p1, p2, ia, ib, w, h, H, r_h, f)
            if res is not None and res.pts_w.shape[0] > best_count:
                best_count = res.pts_w.shape[0]
                best = res
        if best is None or best_count < cfg.init_min_points:
            return None
        if best.median_parallax_deg < cfg.init_min_parallax_deg:
            return None
        return best

    def _with_intrinsics(
        self,
        K: Array,
        p1: Array,
        p2: Array,
        ia: Array,
        ib: Array,
        w: int,
        h: int,
        H: Array | None,
        r_h: float | None,
        focal: float,
    ) -> InitResult | None:
        # ORB-SLAM's model selection: a dominant plane makes the essential matrix
        # degenerate, so prefer the homography when it explains clearly more data.
        if r_h is None:
            H_local, _ = cv2.findHomography(p1, p2, cv2.RANSAC, 2.5, maxIters=2000)
            F_local, _ = cv2.findFundamentalMat(p1, p2, cv2.FM_RANSAC, 1.0, 0.999, 2000)
            if F_local is None or F_local.shape != (3, 3):
                return None
            s_h = _score_homography(H_local, p1, p2) if H_local is not None else 0.0
            s_f = _score_fundamental(F_local, p1, p2)
            r_h = s_h / max(s_h + s_f, 1e-9)
            H = H_local
            focal = float(K[0, 0])
        if r_h > 0.45 and H is not None:
            return self._from_homography(K, H, p1, p2, ia, ib, focal)
        return self._from_essential(K, p1, p2, ia, ib, focal)

    def _from_essential(
        self, K: Array, p1: Array, p2: Array, ia: Array, ib: Array, focal: float
    ) -> InitResult | None:
        E, mask = cv2.findEssentialMat(
            p1, p2, K, method=cv2.RANSAC, prob=0.999, threshold=1.0
        )
        if E is None or E.shape[0] % 3 != 0:
            return None
        E = E[:3]
        n_in, R, t, mask_pose = cv2.recoverPose(E, p1, p2, K, mask=mask)
        if n_in < self.cfg.init_min_inliers:
            return None
        T = np.eye(4)
        T[:3, :3] = R
        T[:3, 3] = t.ravel()          # recoverPose returns a unit baseline
        sel = mask_pose.ravel() > 0
        return self._build(K, T, p1, p2, ia, ib, sel, "essential", focal)

    def _from_homography(
        self, K: Array, H: Array, p1: Array, p2: Array, ia: Array, ib: Array, focal: float
    ) -> InitResult | None:
        n, Rs, ts, _ = cv2.decomposeHomographyMat(H, K)
        best: InitResult | None = None
        best_count = 0
        for i in range(n):
            T = np.eye(4)
            T[:3, :3] = Rs[i]
            T[:3, 3] = ts[i].ravel()
            res = self._build(
                K, T, p1, p2, ia, ib, np.ones(p1.shape[0], dtype=bool), "homography", focal
            )
            if res is not None and res.pts_w.shape[0] > best_count:
                best_count = res.pts_w.shape[0]
                best = res
        return best

    def _build(
        self,
        K: Array,
        T: Array,
        p1: Array,
        p2: Array,
        ia: Array,
        ib: Array,
        sel: Array,
        model: str,
        focal: float,
    ) -> InitResult | None:
        if sel.sum() < self.cfg.init_min_inliers:
            return None
        # Fix the baseline to 1.0 so both model paths produce the same gauge --
        # recoverPose already returns a unit translation but the homography
        # decomposition returns it scaled by the plane distance. Everything
        # downstream is therefore "up to scale", in units of this baseline.
        norm = float(np.linalg.norm(T[:3, 3]))
        if norm < 1e-9:
            return None
        T = T.copy()
        T[:3, 3] /= norm
        P1 = projection_matrix(K, np.eye(4))
        P2 = projection_matrix(K, T)
        pts = triangulate(P1, P2, p1[sel], p2[sel])
        good = _filter_triangulation(K, T, pts, p1[sel], p2[sel], self.cfg)
        if good.sum() < self.cfg.init_min_points:
            return None
        idx = np.flatnonzero(sel)[good]
        pts = pts[good]
        c2 = -T[:3, :3].T @ T[:3, 3]
        med = float(np.median(parallax_deg(pts, np.zeros(3), c2)))
        return InitResult(K, T, pts, ia[idx], ib[idx], model, med, focal)


@dataclass
class TrackResult:
    ok: bool
    T_cw: Array
    kp_idx: Array            # keypoint indices in the current frame that matched
    pt_ids: Array            # corresponding map point ids
    n_inliers: int
    reproj_error_px: float
    mode: str                # "model" | "frame" | "reloc" | "lost"


class Tracker:
    def __init__(self, cfg: SlamConfig, world: Map, K: Array, matcher: Matcher) -> None:
        self.cfg = cfg
        self.world = world
        self.K = K
        self.matcher = matcher
        self.T_cw = np.eye(4)
        self.velocity = np.eye(4)
        self.has_velocity = False
        self.ref_kf_id = 0
        self.last_kp: Array = np.zeros((0, 2), np.float32)
        self.last_desc: Array = np.zeros((0, 32), np.uint8)
        self.last_pt_ids: Array = np.zeros(0, np.int32)
        self.lost_events = 0
        self.lost_frames = 0

    def predict(self) -> Array:
        """Constant-velocity prediction, dead-reckoned across dropped frames.

        Freezing the prediction while lost is what turns one bad frame into
        permanent loss: the search windows stop following the camera and nothing
        can ever match again.
        """
        if not self.has_velocity:
            return self.T_cw.copy()
        T = self.T_cw.copy()
        for _ in range(min(self.lost_frames, 8) + 1):
            T = self.velocity @ T
        return T

    def track(self, frame: Frame) -> TrackResult:
        """Two stages, as in ORB-SLAM.

        Stage A bootstraps a pose from the previous frame's points using the
        constant-velocity prediction to place the search windows. Stage B then
        re-projects the whole local map using the *measured* pose from stage A.
        Stage B is what stops the tracker from locking onto its own prediction:
        if the search windows are only ever centred on the prediction, the only
        matches that survive are the ones consistent with it, and the trajectory
        collapses towards "no motion".
        """
        guess = self.predict()
        cfg = self.cfg
        radius = self._search_radius(guess)
        seed = self._match_and_solve(frame, guess, self._previous_points(), radius, "model")
        if not seed.ok:
            seed = self._match_and_solve(
                frame, guess, self._previous_points(), radius * 2.5, "model"
            )
        if not seed.ok:
            # Descriptor-only match against the previous frame: no positional
            # prior at all, so a wrong prediction cannot poison it.
            seed = self._track_last_frame(frame, guess)
        if not seed.ok and self.lost_frames:
            # Recovering from a dropped frame: the uncertainty has grown with the
            # dead-reckoning, so open the window in proportion.
            wide = cfg.track_search_radius_px * (2.0 + 1.5 * min(self.lost_frames, 6))
            seed = self._track_local_map(frame, guess, wide)
        if not seed.ok:
            self.lost_frames += 1
            return seed

        res = self._track_local_map(frame, seed.T_cw, cfg.track_search_radius_px)
        if not res.ok or res.n_inliers < seed.n_inliers:
            res = seed
        steps = self.lost_frames + 1
        delta = res.T_cw @ se3_inv(self.T_cw)
        self.velocity = se3_exp(se3_log(delta) / steps) if steps > 1 else delta
        self.has_velocity = True
        self.T_cw = res.T_cw
        self.lost_frames = 0
        return res

    def _search_radius(self, guess: Array) -> float:
        """Grow the match window with the rotation the motion model predicts.

        A fixed 14 px window is fine at the 1.2 deg/frame a handheld camera
        averages, but real footage contains pans of 4-8 deg/frame, which move
        features 35-70 px. On samples/desk_handheld_tum.mp4 that is exactly where
        tracking died -- one fast pan at frame 88 and it never recovered.
        """
        cfg = self.cfg
        if not self.has_velocity:
            return cfg.track_search_radius_px
        dR = guess[:3, :3] @ self.T_cw[:3, :3].T
        ang = float(np.linalg.norm(cv2.Rodrigues(dR)[0]))
        rot_px = float(self.K[0, 0]) * np.tan(min(ang, 0.5))
        base = cfg.track_search_radius_px
        return float(np.clip(base + rot_px, base, base * cfg.track_radius_max_scale))

    def _previous_points(self) -> Array:
        have = self.last_pt_ids >= 0
        ids = self.last_pt_ids[have]
        return ids[~self.world.points.bad[ids]] if ids.size else ids

    def _track_local_map(self, frame: Frame, pose: Array, radius: float) -> TrackResult:
        world = self.world
        if not world.keyframes:
            return TrackResult(False, pose, *_empty(), 0, 0.0, "lost")
        ref_kf = world.keyframes[min(self.ref_kf_id, len(world.keyframes) - 1)]
        ids = world.local_point_ids(ref_kf, self.cfg.track_local_kf_window)
        return self._match_and_solve(frame, pose, ids, radius, "model")

    def _match_ratio(self, radius: float) -> float:
        """Tighten the ratio test as the search window opens.

        The window and the descriptor test together set the outlier rate. Holding
        the ratio at its loose value while the window grows for a fast pan drops
        the PnP inlier ratio far enough to lose tracking outright.
        """
        cfg = self.cfg
        span = max(cfg.track_radius_max_scale - 1.0, 1e-6)
        grown = np.clip((radius / cfg.track_search_radius_px - 1.0) / span, 0.0, 1.0)
        loose, tight = cfg.track_match_ratio, cfg.track_match_ratio_tight
        return float(loose - grown * (loose - tight))

    def _match_and_solve(
        self, frame: Frame, pose: Array, ids: Array, radius: float, mode: str
    ) -> TrackResult:
        world = self.world
        if ids.size < 10:
            return TrackResult(False, pose, *_empty(), 0, 0.0, "lost")
        uv, z = project(self.K, pose, world.points.xyz[ids])
        h, w = frame.gray.shape[:2]
        vis = (
            (z > 1e-3)
            & (uv[:, 0] > -radius)
            & (uv[:, 0] < w + radius)
            & (uv[:, 1] > -radius)
            & (uv[:, 1] < h + radius)
        )
        if int(vis.sum()) < 10:
            return TrackResult(False, pose, *_empty(), 0, 0.0, "lost")
        ids = ids[vis]
        mask = proximity_mask(uv[vis], frame.kps, radius)
        ia, ib, dist = self.matcher.ratio_match(
            world.points.desc[ids],
            frame.desc,
            mask=mask,
            ratio=self.cfg.track_match_ratio,
            max_distance=self.cfg.track_match_max_distance
        )
        ia, ib = dedupe_by_distance(ia, ib, dist)
        if ia.size < self.cfg.track_min_inliers:
            return TrackResult(False, pose, *_empty(), 0, 0.0, "lost")
        return self._solve(frame, pose, ids[ia], ib, mode)

    def _track_last_frame(self, frame: Frame, guess: Array) -> TrackResult:
        """Fallback: match directly against the previous frame's associated points."""
        have = self.last_pt_ids >= 0
        if int(have.sum()) < self.cfg.track_min_inliers:
            return TrackResult(False, guess, *_empty(), 0, 0.0, "lost")
        ia, ib = self.matcher.mutual_match(self.last_desc[have], frame.desc, ratio=0.8)
        if ia.size < self.cfg.track_min_inliers:
            return TrackResult(False, guess, *_empty(), 0, 0.0, "lost")
        pt_ids = self.last_pt_ids[have][ia]
        alive = ~self.world.points.bad[pt_ids]
        if int(alive.sum()) < self.cfg.track_min_inliers:
            return TrackResult(False, guess, *_empty(), 0, 0.0, "lost")
        return self._solve(frame, guess, pt_ids[alive], ib[alive], "frame")

    def _solve(
        self, frame: Frame, guess: Array, pt_ids: Array, kp_idx: Array, mode: str
    ) -> TrackResult:
        xyz = self.world.points.xyz[pt_ids]
        uv = frame.kps[kp_idx].astype(np.float64)
        out = refine_pose_pnp(
            self.K,
            xyz,
            uv,
            guess,
            self.cfg.track_pnp_reproj_px,
            self.cfg.track_min_inliers,
        )
        if out is None:
            return TrackResult(False, guess, *_empty(), 0, 0.0, mode)
        T_cw, inl = out
        err = np.linalg.norm(project(self.K, T_cw, xyz[inl])[0] - uv[inl], axis=1)
        return TrackResult(
            True, T_cw, kp_idx[inl], pt_ids[inl], int(inl.size), float(err.mean()), mode
        )

    def relocalise(self, frame: Frame, candidates: list[KeyFrame]) -> TrackResult:
        """BoW-proposed keyframes -> descriptor match -> PnP from scratch."""
        best = TrackResult(False, self.T_cw, *_empty(), 0, 0.0, "lost")
        for kf in candidates:
            have = np.flatnonzero(kf.point_ids >= 0)
            if have.size < 20:
                continue
            ia, ib = self.matcher.mutual_match(kf.desc[have], frame.desc, ratio=0.8)
            if ia.size < self.cfg.relocalise_min_inliers:
                continue
            pt_ids = kf.point_ids[have][ia]
            alive = ~self.world.points.bad[pt_ids]
            pt_ids, kp = pt_ids[alive], ib[alive]
            if pt_ids.size < self.cfg.relocalise_min_inliers:
                continue
            xyz = self.world.points.xyz[pt_ids]
            uv = frame.kps[kp].astype(np.float64)
            ok, rvec, tvec, inliers = cv2.solvePnPRansac(
                xyz,
                uv,
                self.K,
                None,
                iterationsCount=300,
                reprojectionError=self.cfg.track_pnp_reproj_px * 1.5,
                confidence=0.99,
                flags=cv2.SOLVEPNP_EPNP,
            )
            if not ok or inliers is None or inliers.size < self.cfg.relocalise_min_inliers:
                continue
            if inliers.size > best.n_inliers:
                idx = inliers.ravel()
                rvec, tvec = cv2.solvePnPRefineLM(xyz[idx], uv[idx], self.K, None, rvec, tvec)
                T = np.eye(4)
                T[:3, :3] = cv2.Rodrigues(rvec)[0]
                T[:3, 3] = tvec.ravel()
                err = np.linalg.norm(project(self.K, T, xyz[idx])[0] - uv[idx], axis=1)
                best = TrackResult(
                    True, T, kp[idx], pt_ids[idx], int(idx.size), float(err.mean()), "reloc"
                )
                self.ref_kf_id = kf.kf_id
        if best.ok:
            self.T_cw = best.T_cw
            self.velocity = np.eye(4)
            self.has_velocity = False
            self.lost_frames = 0
        return best

    def remember(self, frame: Frame, res: TrackResult) -> None:
        self.last_kp = frame.kps
        self.last_desc = frame.desc
        ids = np.full(frame.kps.shape[0], -1, dtype=np.int32)
        if res.ok and res.kp_idx.size:
            ids[res.kp_idx] = res.pt_ids
        self.last_pt_ids = ids


def _empty() -> tuple[Array, Array]:
    return np.zeros(0, np.int32), np.zeros(0, np.int32)
