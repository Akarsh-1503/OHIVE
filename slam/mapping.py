"""Map storage, keyframe insertion, point culling and local bundle adjustment.

The map is a struct-of-arrays, not a graph of Python objects: every hot query
(project the local map, gather BA observations, cull) is then a vectorised NumPy
op instead of a per-point interpreter loop, which is the difference between
hitting and missing the 30 fps budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import coo_matrix

from .config import SlamConfig
from .geometry import project, se3_inv

Array = np.ndarray


@dataclass
class KeyFrame:
    kf_id: int
    frame_index: int
    t_s: float
    T_cw: Array                      # (4, 4) camera-from-world
    kps: Array                       # (n, 2) float32
    octaves: Array                   # (n,) int32
    desc: Array                      # (n, 32) uint8
    point_ids: Array                 # (n,) int32, -1 where unassigned
    covis: dict[int, int] = field(default_factory=dict)
    bow_words: Array | None = None
    bow_vals: Array | None = None

    @property
    def center(self) -> Array:
        R = self.T_cw[:3, :3]
        return -R.T @ self.T_cw[:3, 3]

    def observations(self) -> tuple[Array, Array]:
        """(point ids, pixel coords) for the assigned keypoints of this keyframe."""
        idx = np.flatnonzero(self.point_ids >= 0)
        return self.point_ids[idx], self.kps[idx]


class PointStore:
    """Growable struct-of-arrays for map points."""

    def __init__(self, capacity: int = 8192) -> None:
        self.n = 0
        self.xyz = np.zeros((capacity, 3), dtype=np.float64)
        self.desc = np.zeros((capacity, 32), dtype=np.uint8)
        self.rgb = np.zeros((capacity, 3), dtype=np.uint8)
        self.obs_count = np.zeros(capacity, dtype=np.int32)
        self.ref_kf = np.zeros(capacity, dtype=np.int32)
        self.first_kf = np.zeros(capacity, dtype=np.int32)
        self.bad = np.zeros(capacity, dtype=bool)

    def _grow(self, extra: int) -> None:
        cap = self.xyz.shape[0]
        need = self.n + extra
        if need <= cap:
            return
        new_cap = max(need, cap * 2)
        for name in ("xyz", "desc", "rgb", "obs_count", "ref_kf", "first_kf", "bad"):
            arr = getattr(self, name)
            shape = (new_cap,) + arr.shape[1:]
            grown = np.zeros(shape, dtype=arr.dtype)
            grown[: self.n] = arr[: self.n]
            setattr(self, name, grown)

    def add(self, xyz: Array, desc: Array, rgb: Array, kf_id: int) -> Array:
        k = xyz.shape[0]
        self._grow(k)
        ids = np.arange(self.n, self.n + k, dtype=np.int32)
        self.xyz[ids] = xyz
        self.desc[ids] = desc
        self.rgb[ids] = rgb
        self.obs_count[ids] = 0
        self.ref_kf[ids] = kf_id
        self.first_kf[ids] = kf_id
        self.bad[ids] = False
        self.n += k
        return ids

    @property
    def alive(self) -> Array:
        return ~self.bad[: self.n]


class Map:
    def __init__(self, cfg: SlamConfig) -> None:
        self.cfg = cfg
        self.points = PointStore()
        self.keyframes: list[KeyFrame] = []
        self.scene_scale = 1.0       # median point depth at init; thresholds scale with it
        self._obs_cache: tuple[Array, Array] | None = None

    # -- bookkeeping --------------------------------------------------------
    def invalidate(self) -> None:
        self._obs_cache = None

    def observation_arrays(self) -> tuple[Array, Array]:
        """Flat (kf_id, point_id) observation lists, rebuilt lazily after edits."""
        if self._obs_cache is None:
            kf_ids: list[Array] = []
            pt_ids: list[Array] = []
            for kf in self.keyframes:
                idx = np.flatnonzero(kf.point_ids >= 0)
                pt_ids.append(kf.point_ids[idx])
                kf_ids.append(np.full(idx.size, kf.kf_id, dtype=np.int32))
            if kf_ids:
                self._obs_cache = (np.concatenate(kf_ids), np.concatenate(pt_ids))
            else:
                self._obs_cache = (np.zeros(0, np.int32), np.zeros(0, np.int32))
        return self._obs_cache

    def add_keyframe(self, kf: KeyFrame) -> None:
        self.keyframes.append(kf)
        self.invalidate()
        sel = kf.point_ids >= 0
        ids = kf.point_ids[sel]
        if ids.size:
            np.add.at(self.points.obs_count, ids, 1)
            # Refresh the representative descriptor from the newest view. A map
            # point that keeps the descriptor it was born with stops matching
            # after ~10 frames of viewpoint change, and the tracked count decays
            # by ~30% per frame until tracking dies.
            self.points.desc[ids] = kf.desc[sel]
        self.update_covisibility(kf)

    def update_covisibility(self, kf: KeyFrame) -> None:
        ids = kf.point_ids[kf.point_ids >= 0]
        if ids.size == 0:
            return
        obs_kf, obs_pt = self.observation_arrays()
        if obs_kf.size == 0:
            return
        shared = np.isin(obs_pt, ids)
        counts = np.bincount(obs_kf[shared], minlength=len(self.keyframes))
        kf.covis.clear()
        for other_id in np.flatnonzero(counts >= 15):
            if int(other_id) == kf.kf_id:
                continue
            c = int(counts[other_id])
            kf.covis[int(other_id)] = c
            self.keyframes[int(other_id)].covis[kf.kf_id] = c

    def covisible(self, kf: KeyFrame, limit: int) -> list[KeyFrame]:
        ranked = sorted(kf.covis.items(), key=lambda kv: -kv[1])[:limit]
        return [self.keyframes[i] for i, _ in ranked]

    def assign(self, kf: KeyFrame, kp_idx: Array, pt_ids: Array) -> None:
        fresh = kf.point_ids[kp_idx] < 0
        kf.point_ids[kp_idx[fresh]] = pt_ids[fresh]
        np.add.at(self.points.obs_count, pt_ids[fresh], 1)
        self.points.desc[pt_ids[fresh]] = kf.desc[kp_idx[fresh]]
        self.invalidate()

    # -- local map ----------------------------------------------------------
    def local_point_ids(self, kf: KeyFrame, window: int) -> Array:
        kfs = [kf, *self.covisible(kf, window)]
        ids = np.concatenate([k.point_ids[k.point_ids >= 0] for k in kfs])
        ids = np.unique(ids)
        ids = ids[~self.points.bad[ids]]
        # A non-finite coordinate can still reach here after a degenerate solve,
        # and it poisons every projection it touches (RuntimeWarning: invalid
        # value encountered in matmul, then a silently empty match set). Retire
        # the point permanently rather than filtering it on every future frame.
        finite = np.isfinite(self.points.xyz[ids]).all(axis=1)
        if not finite.all():
            self.points.bad[ids[~finite]] = True
            ids = ids[finite]
        return ids

    # -- culling ------------------------------------------------------------
    def cull_points(self, recent_ids: Array, current_kf_id: int) -> int:
        """Drop points that failed to establish support after a grace period.

        Mirrors ORB-SLAM's recent-map-point culling: a point that only two
        keyframes ever agreed on is usually a triangulated mismatch.
        """
        if recent_ids.size == 0:
            return 0
        cfg = self.cfg
        ids = recent_ids[~self.points.bad[recent_ids]]
        aged = (current_kf_id - self.points.first_kf[ids]) >= 3
        weak = self.points.obs_count[ids] < cfg.map_point_min_obs
        drop = ids[aged & weak]
        if drop.size:
            self.points.bad[drop] = True
            self._detach(drop)
        return int(drop.size)

    def _detach(self, pt_ids: Array) -> None:
        lookup = np.zeros(self.points.n, dtype=bool)
        lookup[pt_ids] = True
        for kf in self.keyframes:
            sel = kf.point_ids >= 0
            if not sel.any():
                continue
            hit = np.zeros_like(sel)
            hit[sel] = lookup[kf.point_ids[sel]]
            if hit.any():
                kf.point_ids[hit] = -1
        self.invalidate()

    # -- diagnostics --------------------------------------------------------
    def point_reprojection_errors(self, K: Array) -> Array:
        """Mean reprojection error per map point over the keyframes that see it."""
        n = self.points.n
        total = np.zeros(n)
        count = np.zeros(n, dtype=np.int64)
        for kf in self.keyframes:
            pt_ids, uv = kf.observations()
            if pt_ids.size == 0:
                continue
            proj, z = project(K, kf.T_cw, self.points.xyz[pt_ids])
            ok = z > 1e-6
            if not ok.any():
                continue
            err = np.linalg.norm(proj[ok] - uv[ok], axis=1)
            np.add.at(total, pt_ids[ok], err)
            np.add.at(count, pt_ids[ok], 1)
        return (total / np.maximum(count, 1)).astype(np.float32)

    def mean_reprojection_error(self, K: Array, kf_ids: list[int] | None = None) -> float:
        errs: list[Array] = []
        kfs = (
            self.keyframes
            if kf_ids is None
            else [self.keyframes[i] for i in kf_ids]
        )
        for kf in kfs:
            pt_ids, uv = kf.observations()
            if pt_ids.size == 0:
                continue
            proj, z = project(K, kf.T_cw, self.points.xyz[pt_ids])
            ok = z > 1e-6
            if ok.any():
                errs.append(np.linalg.norm(proj[ok] - uv[ok], axis=1))
        if not errs:
            return 0.0
        return float(np.concatenate(errs).mean())


# --------------------------------------------------------------------------
# Local bundle adjustment
# --------------------------------------------------------------------------
@dataclass
class BAResult:
    ran: bool
    error_before_px: float
    error_after_px: float
    n_poses: int
    n_points: int
    n_observations: int
    removed_observations: int
    focal_px: float = 0.0


def local_bundle_adjust(
    world: Map,
    K: Array,
    cfg: SlamConfig,
    free_focal: bool = False,
    structure_only: bool = False,
) -> BAResult:
    """Sliding-window sparse Levenberg-Marquardt over the newest keyframes.

    Free variables: the `ba_window` newest keyframe poses plus every map point
    they observe. Keyframes outside the window that also observe those points
    enter as *fixed* anchors, which is what stops the window from drifting away
    from the older map -- the cheap stand-in for marginalisation.

    Poses are parameterised incrementally as ``T_cw = [R(w), v] @ T0_cw`` with
    (w, v) starting at zero, so the Rodrigues vector never approaches its pi
    singularity no matter where the camera is in the loop. That also makes the
    Jacobian exact and closed-form, which matters: scipy's finite-difference
    path with `jac_sparsity` needs ~45 residual evaluations per Jacobian here,
    the analytic one needs zero.
    """
    kfs = world.keyframes
    if len(kfs) < 3:
        return BAResult(False, 0.0, 0.0, 0, 0, 0, 0)

    window = [kf for kf in kfs[-cfg.ba_window :] if (kf.point_ids >= 0).any()]
    obs_kf, obs_pt = world.observation_arrays()

    def free_after(n_fixed: int) -> tuple[list[KeyFrame], list[int], Array, Array]:
        free = window[n_fixed:]
        ids = {k.kf_id for k in free}
        pts = np.unique(np.concatenate([k.point_ids[k.point_ids >= 0] for k in free]))
        pts = pts[~world.points.bad[pts]]
        lookup = np.full(world.points.n, -1, dtype=np.int32)
        lookup[pts] = np.arange(pts.size, dtype=np.int32)
        anchors = sorted(set(obs_kf[lookup[obs_pt] >= 0].tolist()) - ids)
        return free, anchors, pts, lookup

    if not structure_only and len(window) < 2:
        return BAResult(False, 0.0, 0.0, 0, 0, 0, 0)
    if structure_only:
        # Points free, every pose fixed. Used after a loop closure: pose-graph
        # optimisation has already decided the trajectory, and re-opening the
        # poses lets a full BA trade trajectory accuracy for reprojection error.
        # This restores map-to-pose consistency and nothing else.
        all_pts = np.unique(obs_pt) if obs_pt.size else np.zeros(0, np.int32)
        all_pts = all_pts[~world.points.bad[all_pts]]
        if all_pts.size < 10:
            return BAResult(False, 0.0, 0.0, 0, 0, 0, 0, float(K[0, 0]))
        lookup = np.full(world.points.n, -1, dtype=np.int32)
        lookup[all_pts] = np.arange(all_pts.size, dtype=np.int32)
        free_kfs, anchor_ids, pt_ids, pt_lookup = [], sorted(set(obs_kf.tolist())), all_pts, lookup
    else:
        free_kfs, anchor_ids, pt_ids, pt_lookup = free_after(1)
    if not structure_only and len(anchor_ids) < 2 and len(window) > 2:
        # Monocular gauge: a *single* fixed camera leaves the overall scale free
        # -- rescaling every point and every free camera translation about it
        # leaves all projections identical. That null direction let the window
        # shrink by ~10% per run early on, which reads downstream as violent
        # scale drift. Two fixed cameras remove it.
        free_kfs, anchor_ids, pt_ids, pt_lookup = free_after(2)
    if (not free_kfs and not structure_only) or pt_ids.size < 10:
        return BAResult(False, 0.0, 0.0, 0, 0, 0, 0)
    in_window = pt_lookup[obs_pt] >= 0
    all_kfs = free_kfs + [kfs[i] for i in anchor_ids]
    kf_slot = {kf.kf_id: i for i, kf in enumerate(all_kfs)}

    sel = in_window & np.isin(obs_kf, np.fromiter(kf_slot, dtype=np.int32, count=len(kf_slot)))
    if int(sel.sum()) < cfg.ba_min_observations:
        return BAResult(False, 0.0, 0.0, 0, 0, 0, 0)

    # kf.observations() enumerates in the same order as observation_arrays(),
    # so a positional cursor recovers the pixel coordinate for each selected row.
    cam_slot_list: list[Array] = []
    pt_slot_list: list[Array] = []
    uv_list: list[Array] = []
    cursor = 0
    for kf in kfs:
        idx = np.flatnonzero(kf.point_ids >= 0)
        n = idx.size
        if n == 0:
            continue
        block = sel[cursor : cursor + n]
        cursor += n
        if kf.kf_id not in kf_slot or not block.any():
            continue
        take = idx[block]
        cam_slot_list.append(np.full(take.size, kf_slot[kf.kf_id], dtype=np.int32))
        pt_slot_list.append(pt_lookup[kf.point_ids[take]])
        uv_list.append(kf.kps[take].astype(np.float64))
    cam_slot = np.concatenate(cam_slot_list)
    pt_slot = np.concatenate(pt_slot_list)
    uv = np.concatenate(uv_list)

    n_free = len(free_kfs)
    n_pts = int(pt_ids.size)
    n_obs = int(cam_slot.size)

    R0 = np.stack([kf.T_cw[:3, :3] for kf in all_kfs])
    t0 = np.stack([kf.T_cw[:3, 3] for kf in all_kfs])
    # Focal self-calibration: one shared fx = fy parameter, carried as a log so the
    # step is scale-relative and the value can never go negative.
    nf = 1 if free_focal else 0
    x0 = np.concatenate(
        [np.zeros(nf), np.zeros(n_free * 6), world.points.xyz[pt_ids].ravel()]
    )

    f0, cx, cy = K[0, 0], K[0, 2], K[1, 2]
    free_mask = cam_slot < n_free
    free_obs = np.flatnonzero(free_mask)
    free_cam = cam_slot[free_obs]

    def focal(x: Array) -> float:
        return f0 * float(np.exp(x[0])) if free_focal else f0

    def world_points(x: Array) -> Array:
        return x[nf + n_free * 6 :].reshape(n_pts, 3)

    def cameras(x: Array) -> tuple[Array, Array, Array]:
        """Effective (A, b) with Xc = A @ Xw + b, plus the incremental rotations."""
        d = x[nf : nf + n_free * 6].reshape(n_free, 6)
        A = R0.copy()
        b = t0.copy()
        if n_free:
            Rw = np.stack([cv2.Rodrigues(dd[:3])[0] for dd in d])
            A[:n_free] = np.einsum("nij,njk->nik", Rw, R0[:n_free])
            b[:n_free] = np.einsum("nij,nj->ni", Rw, t0[:n_free]) + d[:, 3:]
        return A, b, d

    def residuals(x: Array) -> Array:
        A, b, _ = cameras(x)
        f = focal(x)
        pts = world_points(x)
        Xc = np.einsum("nij,nj->ni", A[cam_slot], pts[pt_slot]) + b[cam_slot]
        z = np.where(np.abs(Xc[:, 2]) < 1e-6, 1e-6, Xc[:, 2])
        out = np.empty((n_obs, 2))
        out[:, 0] = f * (Xc[:, 0] / z) + cx - uv[:, 0]
        out[:, 1] = f * (Xc[:, 1] / z) + cy - uv[:, 1]
        return out.ravel()

    def jacobian(x: Array) -> coo_matrix:
        A, b, d = cameras(x)
        f = focal(x)
        fx = fy = f
        pts = world_points(x)
        Xw = pts[pt_slot]
        Xc = np.einsum("nij,nj->ni", A[cam_slot], Xw) + b[cam_slot]
        z = np.where(np.abs(Xc[:, 2]) < 1e-6, 1e-6, Xc[:, 2])
        inv_z = 1.0 / z
        Jp = np.zeros((n_obs, 2, 3))
        Jp[:, 0, 0] = fx * inv_z
        Jp[:, 0, 2] = -fx * Xc[:, 0] * inv_z * inv_z
        Jp[:, 1, 1] = fy * inv_z
        Jp[:, 1, 2] = -fy * Xc[:, 1] * inv_z * inv_z
        J_point = np.einsum("nij,njk->nik", Jp, A[cam_slot])

        rows: list[Array] = []
        cols: list[Array] = []
        vals: list[Array] = []
        obs_idx = np.arange(n_obs)

        if free_obs.size:
            # cv2 returns dR/dr as (3, 9) with row k holding dR/dr_k row-major.
            dRdw = np.stack(
                [cv2.Rodrigues(dd[:3])[1].reshape(3, 3, 3) for dd in d]
            )
            Y = (
                np.einsum("nij,nj->ni", R0[free_cam], Xw[free_obs]) + t0[free_cam]
            )
            dXc_dw = np.einsum("nkij,nj->nik", dRdw[free_cam], Y)
            J_rot = np.einsum("nij,njk->nik", Jp[free_obs], dXc_dw)
            base = nf + free_cam * 6
            for comp in range(3):
                for axis in range(2):
                    rows.append(2 * free_obs + axis)
                    cols.append(base + comp)
                    vals.append(J_rot[:, axis, comp])
                    rows.append(2 * free_obs + axis)
                    cols.append(base + 3 + comp)
                    vals.append(Jp[free_obs, axis, comp])

        if free_focal:
            # d(u,v)/d(log f) = f * (x/z, y/z)
            inv = 1.0 / np.where(np.abs(Xc[:, 2]) < 1e-6, 1e-6, Xc[:, 2])
            rows.append(2 * obs_idx)
            cols.append(np.zeros(n_obs, dtype=np.int64))
            vals.append(f * Xc[:, 0] * inv)
            rows.append(2 * obs_idx + 1)
            cols.append(np.zeros(n_obs, dtype=np.int64))
            vals.append(f * Xc[:, 1] * inv)

        pbase = nf + n_free * 6 + pt_slot * 3
        for comp in range(3):
            for axis in range(2):
                rows.append(2 * obs_idx + axis)
                cols.append(pbase + comp)
                vals.append(J_point[:, axis, comp])

        return coo_matrix(
            (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
            shape=(2 * n_obs, nf + n_free * 6 + n_pts * 3),
        )

    r0 = np.linalg.norm(residuals(x0).reshape(-1, 2), axis=1)
    err_before = float(r0.mean())
    # Widen the robust kernel when the whole window starts far from the solution:
    # a 2 px kernel against 15 px residuals flattens the cost surface and trf
    # stalls. MAD scaling collapses back to ba_huber_px once tracking is healthy.
    f_scale = max(cfg.ba_huber_px, float(np.median(r0)) * 1.4826)
    try:
        out = least_squares(
            residuals,
            x0,
            jac=jacobian,
            method="trf",
            loss="soft_l1",
            f_scale=f_scale,
            x_scale="jac",
            tr_solver="lsmr",
            # Capping the inner LSMR solve is the single biggest latency knob:
            # scipy's default (tol 1e-8, unbounded iterations) costs ~8x more
            # per step for a step that is no better inside a trust region.
            tr_options={"maxiter": 20, "atol": 1e-3, "btol": 1e-3},
            max_nfev=cfg.ba_max_nfev,
            xtol=1e-6,
            ftol=1e-6,
            gtol=1e-8,
        )
        x = out.x
    except (ValueError, np.linalg.LinAlgError):
        return BAResult(False, err_before, err_before, n_free, n_pts, n_obs, 0, f0)

    err_after = float(np.linalg.norm(residuals(x).reshape(-1, 2), axis=1).mean())
    if not np.isfinite(err_after) or err_after > err_before:
        return BAResult(False, err_before, err_before, n_free, n_pts, n_obs, 0, f0)

    A, b, _ = cameras(x)
    for i, kf in enumerate(free_kfs):
        T = np.eye(4)
        T[:3, :3] = A[i]
        T[:3, 3] = b[i]
        kf.T_cw = T
    world.points.xyz[pt_ids] = world_points(x)

    # Second stage: discard observations the refined solution still cannot
    # explain. These are almost always descriptor mismatches.
    err = np.linalg.norm(residuals(x).reshape(-1, 2), axis=1)
    bad = err > cfg.map_point_max_reproj_px
    removed = 0
    if bad.any():
        removed = _drop_observations(world, all_kfs, cam_slot[bad], pt_ids[pt_slot[bad]])
    return BAResult(True, err_before, err_after, n_free, n_pts, n_obs, removed, focal(x))


def _drop_observations(
    world: Map, all_kfs: list[KeyFrame], cam_slots: Array, point_ids: Array
) -> int:
    removed = 0
    for slot in np.unique(cam_slots):
        kf = all_kfs[int(slot)]
        ids = point_ids[cam_slots == slot]
        hit = np.isin(kf.point_ids, ids) & (kf.point_ids >= 0)
        if hit.any():
            np.add.at(world.points.obs_count, kf.point_ids[hit], -1)
            kf.point_ids[hit] = -1
            removed += int(hit.sum())
    if removed:
        world.invalidate()
    return removed


def structure_only_adjust(world: Map, K: Array, cfg: SlamConfig) -> BAResult:
    """Refit every map point to the current (fixed) keyframe poses."""
    saved = cfg.ba_max_nfev
    try:
        cfg.ba_max_nfev = max(saved, 40)
        return local_bundle_adjust(world, K, cfg, structure_only=True)
    finally:
        cfg.ba_max_nfev = saved


def global_bundle_adjust(world: Map, K: Array, cfg: SlamConfig) -> BAResult:
    """Full-map BA reusing the local solver with the window opened to everything.

    Run after a loop closure: pose-graph optimisation moves keyframes but map
    points only follow their anchor keyframe, so the map and the trajectory come
    out of a closure mutually inconsistent until this pass reconciles them.
    """
    saved_window, saved_nfev = cfg.ba_window, cfg.ba_max_nfev
    try:
        cfg.ba_window = len(world.keyframes)
        cfg.ba_max_nfev = cfg.global_ba_max_nfev
        return local_bundle_adjust(world, K, cfg)
    finally:
        cfg.ba_window, cfg.ba_max_nfev = saved_window, saved_nfev


def refine_pose_pnp(
    K: Array,
    pts_w: Array,
    uv: Array,
    T_cw_guess: Array,
    reproj_px: float,
    min_inliers: int,
) -> tuple[Array, Array] | None:
    """Unseeded PnP RANSAC, then an LM polish on the consensus set.

    The motion model is deliberately *not* passed as an extrinsic guess:
    ``solvePnPRansac`` with ``useExtrinsicGuess`` refines every minimal-sample
    hypothesis from that guess, so a constant-velocity prediction of "nearly
    stationary" reproduces itself and the trajectory stalls. The prediction is
    already used where it belongs -- to place the guided-match search windows.
    """
    if pts_w.shape[0] < 6:
        return None
    ok, rvec, tvec, inliers = cv2.solvePnPRansac(
        pts_w.astype(np.float64),
        uv.astype(np.float64),
        K,
        None,
        iterationsCount=120,
        reprojectionError=reproj_px,
        confidence=0.995,
        flags=cv2.SOLVEPNP_EPNP,
    )
    if not ok or inliers is None or inliers.size < min_inliers:
        return None
    idx = inliers.ravel()
    rvec, tvec = cv2.solvePnPRefineLM(
        pts_w[idx].astype(np.float64), uv[idx].astype(np.float64), K, None, rvec, tvec
    )
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(rvec)[0]
    T[:3, 3] = tvec.ravel()
    return T, idx


def pose_from_T_wc(T_cw: Array) -> Array:
    return se3_inv(T_cw)
