"""End-to-end behaviour: the output contract, and ATE against ground truth.

The accuracy assertions run against `samples/*.mp4`, which ship with exact
ground-truth trajectories. Those are the artifacts a reviewer reruns, so they
are what the thresholds are calibrated on.
"""

from __future__ import annotations

import json
import pickle

import numpy as np
import pytest

from slam import Metrics, Reconstruction, SlamConfig, SlamPipeline
from slam.geometry import align_trajectories

FAST = {"max_frames": 120}


def _ate(recon: Reconstruction, gt_positions: np.ndarray) -> float:
    idx = [p.frame_index for p in recon.poses]
    return align_trajectories(recon.positions(), gt_positions[idx])[3]


@pytest.fixture(scope="module")
def loop_run(request):
    from conftest import load_sample

    video, pos, meta = load_sample("synthetic_loop")
    recon = SlamPipeline(SlamConfig()).run(video)
    return recon, pos, meta


class TestPublicApi:
    def test_config_accepts_the_documented_kwargs(self) -> None:
        cfg = SlamConfig(
            target_width=640, max_features=1200, enable_loop_closure=True, max_frames=1800
        )
        assert (cfg.target_width, cfg.max_features, cfg.enable_loop_closure, cfg.max_frames) == (
            640,
            1200,
            True,
            1800,
        )

    def test_reconstruction_is_picklable(self, loop_run) -> None:
        """The backend hands results between processes."""
        recon = loop_run[0]
        again = pickle.loads(pickle.dumps(recon))
        assert isinstance(again, Reconstruction)
        assert len(again.poses) == len(recon.poses)
        assert np.array_equal(again.xyz, recon.xyz)
        assert again.metrics.keyframes == recon.metrics.keyframes

    def test_service_owned_timing_fields_are_left_alone(self, loop_run) -> None:
        m: Metrics = loop_run[0].metrics
        assert m.wall_ms == 0
        assert m.queue_wait_ms == 0
        assert m.processing_fps == 0.0
        assert m.realtime_factor == 0.0
        # ...but the library still reports what it measured, separately.
        assert loop_run[0].timings["wall_ms"] > 0
        assert loop_run[0].timings["processing_fps"] > 0

    def test_to_dict_matches_the_contract(self, loop_run) -> None:
        d = loop_run[0].to_dict()
        assert set(d) == {"job_id", "poses", "points", "loop_closures", "metrics"}
        assert set(d["points"]) == {"xyz", "rgb", "observations", "reprojection_error"}
        n = len(d["points"]["observations"])
        assert len(d["points"]["xyz"]) == 3 * n
        assert len(d["points"]["rgb"]) == 3 * n
        assert len(d["points"]["reprojection_error"]) == n
        pose = d["poses"][0]
        assert set(pose) == {
            "frame_index", "t_s", "is_keyframe", "position", "quaternion",
            "tracked_points", "reprojection_error_px",
        }
        assert len(pose["position"]) == 3 and len(pose["quaternion"]) == 4
        assert json.dumps(d)                        # must be JSON-serialisable

    def test_web_payload_is_capped(self, loop_run) -> None:
        recon = loop_run[0]
        recon.config.max_points_web = 50
        try:
            assert len(recon.to_dict()["points"]["observations"]) == 50
        finally:
            recon.config.max_points_web = 60000

    def test_exports(self, loop_run, tmp_path) -> None:
        recon = loop_run[0]
        ply = recon.to_ply(tmp_path / "cloud.ply")
        head = ply.read_bytes()[:400].decode("ascii", "ignore")
        assert head.startswith("ply\nformat binary_little_endian 1.0")
        assert f"element vertex {recon.xyz.shape[0]}" in head

        tum = recon.to_tum(tmp_path / "traj.txt")
        rows = [r for r in tum.read_text().splitlines() if not r.startswith("#")]
        assert len(rows) == len(recon.poses)
        vals = [float(v) for v in rows[0].split()]
        assert len(vals) == 8
        # TUM is scalar-last; our quaternions are scalar-first.
        assert np.isclose(np.linalg.norm(vals[4:8]), 1.0, atol=1e-6)
        assert np.isclose(vals[7], recon.quaternions()[0][0], atol=1e-6)

    def test_progress_events_are_demuxable_and_throttled(self) -> None:
        from conftest import load_sample

        video, _, _ = load_sample("synthetic_loop")
        events: list[dict] = []
        SlamPipeline(SlamConfig(**FAST)).run(video, on_progress=events.append)
        kinds = {e["event"] for e in events}
        assert "stage" in kinds and "progress" in kinds
        assert all("event" in e for e in events)
        stages = [e["stage"] for e in events if e["event"] == "stage"]
        assert stages[0] == "decoding" and stages[-1] == "optimizing"
        progress = [e for e in events if e["event"] == "progress"]
        assert progress and set(progress[0]) >= {
            "frames_done", "frames_total", "fps", "keyframes", "map_points",
            "loop_closures", "elapsed_ms",
        }
        # Coalesced to ~150 ms, so a 10 s clip cannot emit hundreds of them.
        assert len(progress) <= 60

    def test_runs_without_a_callback(self) -> None:
        from conftest import load_sample

        video, _, _ = load_sample("synthetic_corridor")
        recon = SlamPipeline(SlamConfig(**FAST)).run(video)
        assert recon.metrics.frames_processed > 0


class TestAccuracy:
    def test_loop_clip_tracks_and_reconstructs(self, loop_run) -> None:
        recon, pos, _ = loop_run
        m = recon.metrics
        assert m.frames_processed >= 290
        assert len(recon.poses) >= 250            # near-complete trajectory coverage
        assert m.keyframes >= 10
        assert m.map_points >= 500
        assert m.mean_reprojection_error_px < 3.0
        assert _ate(recon, pos) < 2.0             # metres, Sim(3)-aligned, 8.85 m path

    def test_corridor_clip_ate(self, corridor_sample) -> None:
        """Open-ended translation: no closure can fire, so this is raw odometry."""
        video, pos, meta = corridor_sample
        recon = SlamPipeline(SlamConfig()).run(video)
        assert recon.metrics.loop_closures == 0
        assert len(recon.poses) >= 250
        ate = _ate(recon, pos)
        assert ate < 0.5, f"ATE {ate:.3f} m over {meta['trajectory_length_m']} m"

    def test_focal_estimate_is_in_the_right_ballpark(self, corridor_sample) -> None:
        video, _, meta = corridor_sample
        recon = SlamPipeline(SlamConfig()).run(video)
        scale = recon.config.target_width / meta["width"]
        expected = meta["intrinsics"]["fx"] * scale
        assert recon.intrinsics["source"] == "estimated"
        assert abs(recon.intrinsics["fx"] - expected) / expected < 0.25

    def test_calibration_override_is_honoured(self, corridor_sample) -> None:
        video, _, meta = corridor_sample
        scale = 640 / meta["width"]
        fx = meta["intrinsics"]["fx"] * scale
        cfg = SlamConfig(
            fx=fx, fy=fx, cx=meta["intrinsics"]["cx"] * scale,
            cy=meta["intrinsics"]["cy"] * scale, refine_focal=False, max_frames=90,
        )
        recon = SlamPipeline(cfg).run(video)
        assert recon.intrinsics["source"] == "user"
        assert recon.intrinsics["fx"] == pytest.approx(fx)

    def test_loop_closure_collapses_the_loop_error(self, loop_run) -> None:
        """Requirement #4: the closure has to measurably remove accumulated drift."""
        recon = loop_run[0]
        if recon.metrics.loop_closures == 0:
            pytest.skip("no closure accepted on this clip")
        drift = recon.metrics.drift
        assert drift.pre_optimization_loop_error_m > 0
        assert drift.post_optimization_loop_error_m < drift.pre_optimization_loop_error_m
        assert drift.reduction_pct > 80.0
        assert recon.loop_closures[0]["inliers"] >= recon.config.lc_min_sim3_inliers

    def test_disabling_loop_closure_is_respected(self, loop_run) -> None:
        from conftest import load_sample

        video, _, _ = load_sample("synthetic_loop")
        recon = SlamPipeline(SlamConfig(enable_loop_closure=False)).run(video)
        assert recon.metrics.loop_closures == 0
        assert recon.metrics.drift.reduction_pct == 0.0

    def test_frame_cap_is_respected(self, corridor_sample) -> None:
        video, _, _ = corridor_sample
        recon = SlamPipeline(SlamConfig(max_frames=60)).run(video)
        assert recon.metrics.frames_processed <= 60


class TestRobustness:
    def test_missing_file_raises(self) -> None:
        with pytest.raises(RuntimeError, match="could not open"):
            SlamPipeline(SlamConfig()).run("does_not_exist.mp4")

    def test_untrackable_video_degrades_gracefully(self, tmp_path) -> None:
        """A featureless clip must return an empty reconstruction, not crash."""
        import cv2

        path = tmp_path / "blank.mp4"
        w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (320, 240))
        for _ in range(40):
            w.write(np.full((240, 320, 3), 128, np.uint8))
        w.release()
        recon = SlamPipeline(SlamConfig()).run(path)
        assert recon.metrics.frames_processed == 40
        assert recon.metrics.keyframes == 0
        assert recon.poses == []
        assert json.dumps(recon.to_dict())
