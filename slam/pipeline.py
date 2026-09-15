"""Driftless pipeline: video in, sparse map + trajectory + metrics out.

Threading model
---------------
Decode+resize and ORB extraction each get their own thread and feed bounded
queues; tracking, mapping, BA and loop closure stay on the calling thread.
OpenCV releases the GIL inside ``VideoCapture.read``, ``resize`` and ``ORB``,
so those two stages genuinely overlap with tracking. Bundle adjustment does
*not* get a thread: its inner loop is Python plus small NumPy arrays, so it is
GIL-bound and a worker thread would only add lock traffic.

Timing fields follow the API contract. ``decode_ms`` is concurrent work and is
reported as measured; ``tracking_ms`` and ``optimize_ms`` are disjoint slices of
the calling thread and sum to ``wall_ms``.
"""

from __future__ import annotations

import contextlib
import os
import platform
import queue
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import SlamConfig
from .cpu import available_cpus, numeric_threads
from .features import (
    FeatureExtractor,
    Frame,
    Matcher,
    epipolar_mask,
)
from .geometry import (
    parallax_deg,
    project,
    projection_matrix,
    se3_exp,
    se3_inv,
    se3_log,
    triangulate,
)
from .loop import LoopCloser, Vocabulary
from .mapping import (
    KeyFrame,
    Map,
    global_bundle_adjust,
    local_bundle_adjust,
    structure_only_adjust,
)
from .results import DriftMetrics, Metrics, PoseRecord, ProgressFn, Reconstruction
from .tracking import Initializer, Tracker, TrackResult, default_intrinsics

Array = np.ndarray


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------
def _host_info() -> dict[str, Any]:
    cpu = platform.processor() or platform.machine()
    if platform.system() == "Darwin":
        try:
            import subprocess

            cpu = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    elif platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    ram_gb = 0.0
    with contextlib.suppress(ValueError, OSError, AttributeError):
        ram_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    return {"cpu": cpu, "vcpu": available_cpus(), "ram_gb": round(ram_gb, 1)}


class _Decoder(threading.Thread):
    """Decode -> resize -> grayscale, ahead of the tracker."""

    def __init__(self, path: str, cfg: SlamConfig, out: queue.Queue, stop: threading.Event):
        super().__init__(daemon=True)
        self.path = path
        self.cfg = cfg
        self.out = out
        self.stop = stop
        self.busy_ms = 0.0
        self.error: Exception | None = None
        self.info: dict[str, Any] = {}
        self._ready = threading.Event()

    def open(self) -> dict[str, Any]:
        cap = cv2.VideoCapture(self.path, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.path)
        if not cap.isOpened():
            raise RuntimeError(f"could not open video: {self.path}")
        self.cap = cap
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
        if not np.isfinite(fps) or fps <= 0:
            fps = 30.0
        self.info = {
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": fps,
            "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        }
        self.info["duration_s"] = (
            self.info["frame_count"] / fps if self.info["frame_count"] > 0 else 0.0
        )
        return self.info

    def run(self) -> None:
        cfg = self.cfg
        fps = self.info["fps"]
        width = self.info["width"]
        scale = cfg.target_width / width if width > cfg.target_width else 1.0
        raw_index = 0
        emitted = 0
        try:
            while not self.stop.is_set() and emitted < cfg.max_frames:
                t0 = time.perf_counter()
                ok, bgr = self.cap.read()
                if not ok:
                    break
                if raw_index % cfg.frame_stride != 0:
                    raw_index += 1
                    self.busy_ms += (time.perf_counter() - t0) * 1e3
                    continue
                if scale != 1.0:
                    bgr = cv2.resize(
                        bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
                    )
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                self.busy_ms += (time.perf_counter() - t0) * 1e3
                self.out.put((raw_index, raw_index / fps, bgr, gray))
                raw_index += 1
                emitted += 1
        except Exception as exc:                     # surfaced on the main thread
            self.error = exc
        finally:
            self.cap.release()
            self.out.put(None)


class _Extractor(threading.Thread):
    def __init__(
        self,
        cfg: SlamConfig,
        src: queue.Queue,
        out: queue.Queue,
        stop: threading.Event,
    ):
        super().__init__(daemon=True)
        self.fe = FeatureExtractor(cfg)
        self.src = src
        self.out = out
        self.stop = stop
        self.busy_ms = 0.0
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                item = self.src.get()
                if item is None:
                    break
                index, t_s, bgr, gray = item
                t0 = time.perf_counter()
                kps, octaves, desc = self.fe.detect(gray)
                self.busy_ms += (time.perf_counter() - t0) * 1e3
                self.out.put(Frame(index, t_s, gray, bgr, kps, octaves, desc))
        except Exception as exc:
            self.error = exc
        finally:
            self.out.put(None)


class SlamPipeline:
    def __init__(self, cfg: SlamConfig | None = None) -> None:
        self.cfg = cfg or SlamConfig()
        self.matcher = Matcher(self.cfg)
        self.vocab = Vocabulary.load()
        self.stage_times: dict[str, float] = {}

    # -- public API ---------------------------------------------------------
    def run(
        self,
        video_path: str | Path,
        on_progress: ProgressFn | None = None,
        job_id: str = "",
    ) -> Reconstruction:
        """Process a video end to end.

        ``on_progress`` is the single event sink. Every payload carries an
        ``event`` key so the caller can demultiplex it onto the SSE stream:

        * ``{"event": "stage", "stage": ..., "message": ...}``
        * ``{"event": "progress", "frames_done": ..., ...}`` -- coalesced to at
          most one every ``cfg.progress_interval_s`` (150 ms by default)
        * ``{"event": "loop_closure", "from_kf": ..., "to_kf": ..., "inliers": ...}``

        It must be cheap; it is called from the tracking thread.
        """
        cfg = self.cfg
        # OpenCV keeps its own pool and ignores OMP_NUM_THREADS, so it has to be told
        # separately. Left alone if the host application has already set a value.
        if cv2.getNumThreads() > numeric_threads():
            cv2.setNumThreads(numeric_threads())
        t_start = time.perf_counter()
        stop = threading.Event()
        q_raw: queue.Queue = queue.Queue(maxsize=cfg.queue_size)
        q_feat: queue.Queue = queue.Queue(maxsize=cfg.queue_size)

        emit = on_progress or (lambda _ev: None)
        decoder = _Decoder(str(video_path), cfg, q_raw, stop)
        video = decoder.open()
        extractor = _Extractor(cfg, q_raw, q_feat, stop)
        emit(
            {
                "event": "stage",
                "stage": "decoding",
                "message": f"decoding {video['width']}x{video['height']}",
            }
        )
        decoder.start()
        extractor.start()

        self._reset(video)
        last_progress = 0.0
        frames_total = min(video["frame_count"] or cfg.max_frames, cfg.max_frames)
        track_ms = 0.0
        opt_ms = 0.0
        announced_tracking = False

        try:
            while True:
                frame = q_feat.get()
                if frame is None:
                    break
                t0 = time.perf_counter()
                opt_ms += self._step(frame, emit)
                track_ms += (time.perf_counter() - t0) * 1e3
                self.frames_processed += 1
                if self.world.keyframes and not announced_tracking:
                    emit({"event": "stage", "stage": "tracking", "message": "tracking"})
                    announced_tracking = True
                now = time.perf_counter()
                if now - last_progress >= cfg.progress_interval_s:
                    last_progress = now
                    elapsed = (now - t_start) * 1e3
                    emit(
                        {
                            "event": "progress",
                            "frames_done": self.frames_processed,
                            "frames_total": int(frames_total),
                            "fps": round(self.frames_processed / max(elapsed / 1e3, 1e-6), 2),
                            "keyframes": len(self.world.keyframes),
                            "map_points": int(self.world.points.alive.sum()),
                            "loop_closures": len(self.closer.closures),
                            "elapsed_ms": int(elapsed),
                        }
                    )
        finally:
            stop.set()
            # Drain so the producers can exit instead of blocking on a full queue.
            for q in (q_raw, q_feat):
                while not q.empty():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
            decoder.join(timeout=5.0)
            extractor.join(timeout=5.0)

        if decoder.error is not None:
            raise decoder.error
        if extractor.error is not None:
            raise extractor.error

        emit({"event": "stage", "stage": "optimizing", "message": "final optimisation"})
        t0 = time.perf_counter()
        if self._needs_global_ba and len(self.world.keyframes) >= 4:
            res = structure_only_adjust(self.world, self.K, cfg)
            if res.ran:
                self.ba_runs += 1
                self.global_ba = (res.error_before_px, res.error_after_px)
        if cfg.enable_global_ba and len(self.world.keyframes) >= 4:
            res = global_bundle_adjust(self.world, self.K, cfg)
            if res.ran:
                self.ba_runs += 1
                self.global_ba = (res.error_before_px, res.error_after_px)
        opt_ms += (time.perf_counter() - t0) * 1e3

        wall_ms = (time.perf_counter() - t_start) * 1e3
        self.stage_times = {
            "decode_ms": decoder.busy_ms,
            "feature_ms": extractor.busy_ms,
            "track_ms": track_ms - opt_ms,
            "optimize_ms": opt_ms,
            "wall_ms": wall_ms,
        }
        return self._finalise(job_id, video, wall_ms, decoder.busy_ms, track_ms, opt_ms)

    # -- internals ----------------------------------------------------------
    def _reset(self, video: dict[str, Any]) -> None:
        cfg = self.cfg
        width = min(cfg.target_width, video["width"]) if video["width"] else cfg.target_width
        height = int(round(video["height"] * width / max(video["width"], 1)))
        self.K = default_intrinsics(width, height, cfg)
        self.world = Map(cfg)
        self.tracker = Tracker(cfg, self.world, self.K, self.matcher)
        self.initializer = Initializer(cfg, self.matcher)
        self.closer = LoopCloser(cfg, self.world, self.K, self.matcher, self.vocab)
        self.records: list[PoseRecord] = []
        self.kf_scale: list[float] = []
        self.ref_frame: Frame | None = None
        self.initialised = False
        self.frames_processed = 0
        self.frames_since_kf = 0
        self.lost_events = 0
        self.ba_runs = 0
        self.ba_before: list[float] = []
        self.ba_after: list[float] = []
        self.recent_point_ids = np.zeros(0, dtype=np.int32)
        self._needs_global_ba = False
        self.focal_ba: tuple[float, float] | None = None
        self.global_ba: tuple[float, float] | None = None
        self.ref_tracked = 0
        self.focal_px = float(self.K[0, 0])
        self.init_model = "none"

    def _step(self, frame: Frame, emit: ProgressFn) -> float:
        """Process one frame. Returns milliseconds spent in optimisation."""
        if not self.initialised:
            self._try_initialise(frame)
            return 0.0

        res = self.tracker.track(frame)
        if not res.ok and self.tracker.lost_frames >= self.cfg.relocalise_after_frames:
            candidates = self.closer.relocalisation_candidates(frame.desc)
            res = self.tracker.relocalise(frame, candidates)
            self.lost_events += 1
        if not res.ok:
            # Keep the last good frame's associations: clearing them here removes
            # the only bootstrap the next frame has, and one dropped frame then
            # becomes permanent loss.
            return 0.0

        self.tracker.remember(frame, res)
        self.frames_since_kf += 1
        opt_ms = 0.0
        if self._needs_keyframe(res):
            opt_ms = self._insert_keyframe(frame, res, emit)
        self._record(frame, res, is_keyframe=opt_ms > 0.0 or self.frames_since_kf == 0)
        return opt_ms

    def _record(self, frame: Frame, res: TrackResult, is_keyframe: bool) -> None:
        ref_id = self.tracker.ref_kf_id
        ref_kf = self.world.keyframes[ref_id]
        T_cr = res.T_cw @ se3_inv(ref_kf.T_cw)
        self.records.append(
            PoseRecord(
                frame.index,
                frame.t_s,
                is_keyframe,
                ref_id,
                T_cr,
                res.n_inliers,
                res.reproj_error_px,
            )
        )

    # -- initialisation -----------------------------------------------------
    def _try_initialise(self, frame: Frame) -> None:
        cfg = self.cfg
        if frame.kps.shape[0] < cfg.init_min_matches:
            return
        if self.ref_frame is None:
            self.ref_frame = frame
            return
        if frame.index - self.ref_frame.index > cfg.init_max_reference_age:
            self.ref_frame = frame
            return
        out = self.initializer.try_init(self.ref_frame, frame)
        if out is None:
            return

        self.K = out.K
        self.focal_px = out.focal_px
        self.init_model = out.model
        self.tracker.K = out.K
        self.closer.K = out.K
        world = self.world

        ref, cur = self.ref_frame, frame
        n = out.pts_w.shape[0]
        rgb = cur.bgr_small[
            np.clip(cur.kps[out.cur_idx, 1].astype(int), 0, cur.bgr_small.shape[0] - 1),
            np.clip(cur.kps[out.cur_idx, 0].astype(int), 0, cur.bgr_small.shape[1] - 1),
        ][:, ::-1]

        kf0 = KeyFrame(
            0, ref.index, ref.t_s, np.eye(4), ref.kps, ref.octaves, ref.desc,
            np.full(ref.kps.shape[0], -1, np.int32),
        )
        kf1 = KeyFrame(
            1, cur.index, cur.t_s, out.T_cw, cur.kps, cur.octaves, cur.desc,
            np.full(cur.kps.shape[0], -1, np.int32),
        )
        ids = world.points.add(out.pts_w, cur.desc[out.cur_idx], rgb, 0)
        kf0.point_ids[out.ref_idx] = ids
        kf1.point_ids[out.cur_idx] = ids
        world.add_keyframe(kf0)
        world.add_keyframe(kf1)
        world.scene_scale = float(np.median(np.linalg.norm(out.pts_w, axis=1)))
        self.kf_scale = [1.0, 1.0]
        for kf in (kf0, kf1):
            self.closer.add_keyframe(kf)

        self.tracker.T_cw = out.T_cw.copy()
        self.tracker.ref_kf_id = 1
        # The init pair spans several frames; divide the motion down so the
        # constant-velocity model starts with a per-frame step, not a per-pair one.
        gap = max(cur.index - ref.index, 1)
        self.tracker.velocity = se3_exp(se3_log(out.T_cw) / gap)
        self.tracker.has_velocity = True
        self.tracker.remember(cur, TrackResult(True, out.T_cw, out.cur_idx, ids, n, 0.0, "init"))
        self.initialised = True
        self.frames_since_kf = 0
        self.ref_tracked = n
        self.recent_point_ids = ids

        err = world.mean_reprojection_error(out.K)
        for kf in (kf0, kf1):
            self.records.append(
                PoseRecord(
                    kf.frame_index, kf.t_s, True, kf.kf_id, np.eye(4), int(n), err
                )
            )
        self.ref_frame = None

    # -- keyframes ----------------------------------------------------------
    def _needs_keyframe(self, res: TrackResult) -> bool:
        cfg = self.cfg
        if self.frames_since_kf < cfg.kf_min_frame_gap:
            return False
        if res.n_inliers < cfg.track_min_inliers:
            return False
        if self.frames_since_kf >= cfg.kf_max_frame_gap:
            return True
        # Geometric criterion: enough baseline relative to scene depth to
        # triangulate well. The tracked-ratio test alone couples keyframe rate to
        # how many matches the tracker happens to find, so widening the search
        # window suppressed keyframes, starved the map and cost 70 frames of
        # coverage -- the opposite of what widening the window was for.
        if res.pt_ids.size >= 10 and self.world.keyframes:
            last = self.world.keyframes[-1]
            centre = -res.T_cw[:3, :3].T @ res.T_cw[:3, 3]
            baseline = float(np.linalg.norm(centre - last.center))
            Xc = self.world.points.xyz[res.pt_ids] @ res.T_cw[:3, :3].T + res.T_cw[:3, 3]
            depth = float(np.median(Xc[:, 2]))
            if depth > 1e-9 and baseline / depth > cfg.kf_min_parallax_ratio:
                return True
            # Rotation since the last keyframe. Translation-based criteria say
            # nothing about a pan, and a pan is precisely what rotates the old map
            # out of view -- one at frame 88 of the handheld clip cost 151
            # consecutive frames of tracking.
            dR = res.T_cw[:3, :3] @ last.T_cw[:3, :3].T
            if np.degrees(np.linalg.norm(cv2.Rodrigues(dR)[0])) > cfg.kf_min_rotation_deg:
                return True
        weak = res.n_inliers < cfg.kf_tracked_ratio * max(self.ref_tracked, 1)
        return weak or res.n_inliers < cfg.kf_min_tracked

    def _insert_keyframe(self, frame: Frame, res: TrackResult, emit: ProgressFn) -> float:
        cfg = self.cfg
        world = self.world
        kf = KeyFrame(
            len(world.keyframes),
            frame.index,
            frame.t_s,
            res.T_cw.copy(),
            frame.kps,
            frame.octaves,
            frame.desc,
            np.full(frame.kps.shape[0], -1, np.int32),
        )
        kf.point_ids[res.kp_idx] = res.pt_ids
        world.add_keyframe(kf)
        self.kf_scale.append(self.kf_scale[-1] if self.kf_scale else 1.0)
        new_ids = self._triangulate_new(kf, frame)
        self.recent_point_ids = np.unique(np.concatenate([self.recent_point_ids, new_ids]))
        world.cull_points(self.recent_point_ids, kf.kf_id)
        self.recent_point_ids = self.recent_point_ids[
            ~world.points.bad[self.recent_point_ids]
        ][-4000:]

        opt_ms = 0.0
        if (
            cfg.refine_focal_ba
            and cfg.fx is None
            and kf.kf_id == cfg.focal_ba_keyframe
            and len(world.keyframes) > 4
        ):
            t0 = time.perf_counter()
            self._refine_focal(world)
            opt_ms += (time.perf_counter() - t0) * 1e3

        if cfg.enable_local_ba and kf.kf_id % cfg.ba_every_n_keyframes == 0:
            t0 = time.perf_counter()
            out = local_bundle_adjust(world, self.K, cfg)
            opt_ms += (time.perf_counter() - t0) * 1e3
            if out.ran:
                self.ba_runs += 1
                self.ba_before.append(out.error_before_px)
                self.ba_after.append(out.error_after_px)

        self.closer.add_keyframe(kf)
        if cfg.enable_loop_closure:
            t0 = time.perf_counter()
            for cand in self.closer.detect(kf):
                self.closer.candidates_checked += 1
                sim3 = self.closer.verify(kf, cand)
                if sim3 is None:
                    continue
                accepted = self.closer.close(kf, cand, sim3)
                if accepted is None:
                    continue
                closure, old, new = accepted
                self._propagate_closure(old, new)
                if cfg.global_ba_after_closure:
                    self._needs_global_ba = True
                emit(
                    {
                        "event": "loop_closure",
                        "from_kf": closure.from_kf,
                        "to_kf": closure.to_kf,
                        "inliers": closure.inliers,
                    }
                )
                break
            opt_ms += (time.perf_counter() - t0) * 1e3

        self.tracker.T_cw = kf.T_cw.copy()
        self.tracker.ref_kf_id = kf.kf_id
        self.ref_tracked = res.n_inliers
        self.frames_since_kf = 0
        return max(opt_ms, 1e-9)

    def _refine_focal(self, world: Map) -> bool:
        """One wide-window BA with fx = fy free, then freeze the focal again."""
        cfg = self.cfg
        saved_window, saved_nfev = cfg.ba_window, cfg.ba_max_nfev
        cfg.ba_window = len(world.keyframes)
        cfg.ba_max_nfev = max(saved_nfev, 40)
        try:
            out = local_bundle_adjust(world, self.K, cfg, free_focal=True)
        finally:
            cfg.ba_window, cfg.ba_max_nfev = saved_window, saved_nfev
        lo = cfg.focal_search_lo * self.K[0, 2] * 2.0
        hi = cfg.focal_search_hi * self.K[0, 2] * 2.0
        if not out.ran or not (lo < out.focal_px < hi):
            return False
        self.K = self.K.copy()
        self.K[0, 0] = self.K[1, 1] = out.focal_px
        self.focal_px = float(out.focal_px)
        self.tracker.K = self.K
        self.closer.K = self.K
        # Kept out of ba_before/ba_after: this run starts from a deliberately
        # wrong focal, so folding it in would flatter the local-BA figure.
        self.ba_runs += 1
        self.focal_ba = (out.error_before_px, out.error_after_px)
        return True

    def _triangulate_new(self, kf: KeyFrame, frame: Frame) -> Array:
        """Triangulate the keyframe's unmatched features against covisible neighbours."""
        cfg = self.cfg
        world = self.world
        Kinv = np.linalg.inv(self.K)
        c_kf = kf.center
        # Reference depth of the map this keyframe already sees; both the baseline
        # requirement and the depth cap are expressed relative to it.
        seen = kf.point_ids[kf.point_ids >= 0]
        if seen.size >= 10:
            Xc = world.points.xyz[seen] @ kf.T_cw[:3, :3].T + kf.T_cw[:3, 3]
            ref_depth = float(np.median(Xc[:, 2]))
        else:
            ref_depth = world.scene_scale
        ref_depth = max(ref_depth, 1e-6)
        depth_cap = cfg.new_point_depth_ratio * ref_depth
        min_ratio = float(np.tan(np.radians(cfg.triangulate_min_parallax_deg)))

        # Covisibility ranks the *most recent* keyframes highest, and those are
        # exactly the short-baseline, ill-conditioned pairs. Union in the last N
        # keyframes by index as well, then take the longest baselines that still
        # share appearance. Depth bias from two-view triangulation is ~12% at
        # 0.6 degrees of parallax, 1.3% at 1.4 and 0.01% at 5.7, so buying
        # parallax with baseline is worth real effort.
        pool = {k.kf_id: k for k in world.covisible(kf, cfg.triangulate_neighbour_pool)}
        for k in world.keyframes[max(0, kf.kf_id - cfg.triangulate_recent_pool) : kf.kf_id]:
            pool.setdefault(k.kf_id, k)
        ordered = sorted(pool.values(), key=lambda nb: -float(np.linalg.norm(c_kf - nb.center)))
        neighbours = [
            nb
            for nb in ordered
            if float(np.linalg.norm(c_kf - nb.center)) / ref_depth > min_ratio
        ][: cfg.triangulate_neighbours]
        if not neighbours:
            neighbours = ordered[:1]
        # Quality first, quantity second. Long-baseline pairs get first pick of the
        # unmatched keypoints, and the short-baseline ones are only consulted if
        # the keyframe still has too few points to track against. Taking only the
        # long baselines starves the map (coverage fell to 143/300); taking them
        # all lets the ill-conditioned pairs set the scale.
        created: list[Array] = []
        n_created = 0
        for nb in neighbours:
            if n_created >= cfg.triangulate_target_new:
                break
            free_cur = np.flatnonzero(kf.point_ids < 0)
            free_nb = np.flatnonzero(nb.point_ids < 0)
            if free_cur.size < 20 or free_nb.size < 20:
                continue
            # Epipolar-guided search: the relative pose is already known, so
            # constrain the match to the epipolar band and let the ratio test
            # run loose. Unconstrained mutual matching recovers roughly a third
            # as many correspondences and the map starves.
            T_rel = kf.T_cw @ se3_inv(nb.T_cw)
            t_rel = T_rel[:3, 3]
            E = np.array(
                [
                    [0.0, -t_rel[2], t_rel[1]],
                    [t_rel[2], 0.0, -t_rel[0]],
                    [-t_rel[1], t_rel[0], 0.0],
                ]
            ) @ T_rel[:3, :3]
            F = Kinv.T @ E @ Kinv
            mask = epipolar_mask(F.T, kf.kps[free_cur], nb.kps[free_nb], 4.0)
            ia, ib = self.matcher.mutual_match(
                kf.desc[free_cur],
                nb.desc[free_nb],
                ratio=cfg.triangulate_match_ratio,
                mask=mask,
                max_distance=cfg.triangulate_match_max_distance
            )
            if ia.size < 12:
                continue
            i_cur, i_nb = free_cur[ia], free_nb[ib]
            p_cur = kf.kps[i_cur].astype(np.float64)
            p_nb = nb.kps[i_nb].astype(np.float64)
            keep = _sampson(F, p_nb, p_cur) < 3.0
            if int(keep.sum()) < 8:
                continue
            i_cur, i_nb, p_cur, p_nb = i_cur[keep], i_nb[keep], p_cur[keep], p_nb[keep]

            pts = triangulate(
                projection_matrix(self.K, kf.T_cw),
                projection_matrix(self.K, nb.T_cw),
                p_cur,
                p_nb,
            )
            z1 = pts @ kf.T_cw[:3, :3].T[:, 2] + kf.T_cw[2, 3]
            z2 = pts @ nb.T_cw[:3, :3].T[:, 2] + nb.T_cw[2, 3]
            ok = (z1 > 1e-4) & (z2 > 1e-4) & np.isfinite(pts).all(axis=1)
            ok &= z1 < depth_cap
            ok &= parallax_deg(pts, c_kf, nb.center) > cfg.triangulate_min_parallax_deg
            if not ok.any():
                continue
            e1 = np.linalg.norm(project(self.K, kf.T_cw, pts)[0] - p_cur, axis=1)
            e2 = np.linalg.norm(project(self.K, nb.T_cw, pts)[0] - p_nb, axis=1)
            ok &= (e1 < cfg.map_point_max_reproj_px) & (e2 < cfg.map_point_max_reproj_px)
            if not ok.any():
                continue
            sel = np.flatnonzero(ok)
            i_cur, i_nb = i_cur[sel], i_nb[sel]
            rgb = frame.bgr_small[
                np.clip(kf.kps[i_cur, 1].astype(int), 0, frame.bgr_small.shape[0] - 1),
                np.clip(kf.kps[i_cur, 0].astype(int), 0, frame.bgr_small.shape[1] - 1),
            ][:, ::-1]
            ids = world.points.add(pts[sel], kf.desc[i_cur], rgb, kf.kf_id)
            world.assign(kf, i_cur, ids)
            world.assign(nb, i_nb, ids)
            created.append(ids)
            n_created += int(ids.size)
        if not created:
            return np.zeros(0, dtype=np.int32)
        ids = np.concatenate(created)
        world.update_covisibility(kf)
        return ids

    def _propagate_closure(
        self,
        old: list[tuple[float, Array, Array]],
        new: list[tuple[float, Array, Array]],
    ) -> None:
        """Re-scale stored relative frame poses and refresh the tracker state."""
        for i, vertex in enumerate(new):
            self.kf_scale[i] *= float(vertex[0])
        for rec in self.records:
            s = float(new[rec.ref_kf_id][0])
            if abs(s - 1.0) > 1e-12:
                rec.T_cr = rec.T_cr.copy()
                rec.T_cr[:3, 3] *= s
        self.tracker.T_cw = self.world.keyframes[-1].T_cw.copy()
        self.tracker.velocity = np.eye(4)
        self.tracker.has_velocity = False

    # -- output -------------------------------------------------------------
    def _finalise(
        self,
        job_id: str,
        video: dict[str, Any],
        wall_ms: float,
        decode_ms: float,
        track_ms: float,
        opt_ms: float,
    ) -> Reconstruction:
        world = self.world
        for rec in self.records:
            ref = world.keyframes[min(rec.ref_kf_id, len(world.keyframes) - 1)]
            rec.T_cw = rec.T_cr @ ref.T_cw

        alive = np.flatnonzero(world.points.alive & (world.points.obs_count[: world.points.n] >= 2))
        xyz = world.points.xyz[alive].astype(np.float32)
        rgb = world.points.rgb[alive]
        obs = world.points.obs_count[alive]
        perr = world.point_reprojection_errors(self.K)[alive]

        positions = (
            np.array([se3_inv(r.T_cw)[:3, 3] for r in self.records])
            if self.records
            else np.zeros((0, 3))
        )
        traj_len = (
            float(np.linalg.norm(np.diff(positions, axis=0), axis=1).sum())
            if positions.shape[0] > 1
            else 0.0
        )

        closures = self.closer.closures
        drift = DriftMetrics()
        if closures:
            pre = float(np.mean([c.pre_error_m for c in closures]))
            post = float(np.mean([c.post_error_m for c in closures]))
            drift = DriftMetrics(
                pre_optimization_loop_error_m=pre,
                post_optimization_loop_error_m=post,
                reduction_pct=100.0 * (1.0 - post / pre) if pre > 1e-12 else 0.0,
                scale_drift_ratio=float(closures[-1].scale),
            )

        duration = video.get("duration_s") or (
            self.frames_processed / max(video.get("fps", 30.0), 1e-6)
        )
        metrics = Metrics(
            decode_ms=round(decode_ms),
            tracking_ms=round(max(track_ms - opt_ms, 0.0)),
            optimize_ms=round(opt_ms),
            frames_processed=self.frames_processed,
            keyframes=len(world.keyframes),
            map_points=int(alive.size),
            mean_reprojection_error_px=world.mean_reprojection_error(self.K),
            median_track_length=int(np.median(obs)) if obs.size else 0,
            loop_closures=len(closures),
            loop_candidates_checked=self.closer.candidates_checked,
            drift=drift,
            trajectory_length_m=traj_len,
            ba_runs=self.ba_runs,
            host=_host_info(),
        )
        return Reconstruction(
            job_id=job_id,
            poses=self.records,
            xyz=xyz,
            rgb=rgb,
            observations=obs,
            loop_closures=[
                {
                    "from_kf": c.from_kf,
                    "to_kf": c.to_kf,
                    "inliers": c.inliers,
                    "scale": round(c.scale, 6),
                }
                for c in closures
            ],
            metrics=metrics,
            config=self.cfg,
            video=video,
            intrinsics={
                "fx": float(self.K[0, 0]),
                "fy": float(self.K[1, 1]),
                "cx": float(self.K[0, 2]),
                "cy": float(self.K[1, 2]),
                "width": int(self.K[0, 2] * 2),
                "height": int(self.K[1, 2] * 2),
                "source": "user" if self.cfg.fx is not None else "estimated",
                "init_model": self.init_model,
            },
            timings={
                "wall_ms": round(wall_ms, 1),
                "decode_ms": round(decode_ms, 1),
                "feature_ms": round(self.stage_times.get("feature_ms", 0.0), 1),
                "tracking_ms": round(max(track_ms - opt_ms, 0.0), 1),
                "optimize_ms": round(opt_ms, 1),
                "processing_fps": round(self.frames_processed / max(wall_ms / 1e3, 1e-9), 2),
                "realtime_factor": round(duration / max(wall_ms / 1e3, 1e-9), 3),
                "video_duration_s": round(float(duration), 3),
                "ba_before_px": round(self.ba_improvement[0], 4),
                "ba_after_px": round(self.ba_improvement[1], 4),
                "ba_runs": self.ba_runs,
                "focal_ba_px": list(self.focal_ba) if self.focal_ba else None,
                "global_ba_px": list(self.global_ba) if self.global_ba else None,
                "ba_median_px": [
                    round(float(np.median(self.ba_before)), 4) if self.ba_before else 0.0,
                    round(float(np.median(self.ba_after)), 4) if self.ba_after else 0.0,
                ],
                "lost_events": self.lost_events,
            },
            point_errors=perr,
        )

    @property
    def ba_improvement(self) -> tuple[float, float]:
        """Mean reprojection error before and after local BA, over every run."""
        if not self.ba_before:
            return 0.0, 0.0
        return float(np.mean(self.ba_before)), float(np.mean(self.ba_after))


def _sampson(F: Array, p1: Array, p2: Array) -> Array:
    """First-order geometric distance to the epipolar constraint, in pixels."""
    h1 = np.concatenate([p1, np.ones((p1.shape[0], 1))], axis=1)
    h2 = np.concatenate([p2, np.ones((p2.shape[0], 1))], axis=1)
    Fx1 = h1 @ F.T
    Ftx2 = h2 @ F
    num = np.einsum("ij,ij->i", h2, Fx1) ** 2
    den = Fx1[:, 0] ** 2 + Fx1[:, 1] ** 2 + Ftx2[:, 0] ** 2 + Ftx2[:, 1] ** 2
    return np.sqrt(num / np.maximum(den, 1e-12))
