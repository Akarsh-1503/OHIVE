"""Bundle adjustment and pose-graph optimisation against known-answer problems."""

from __future__ import annotations

import numpy as np
import pytest

from slam import geometry as g
from slam.config import SlamConfig
from slam.loop import LoopCloser, PoseGraphEdge, Vocabulary
from slam.mapping import KeyFrame, Map, local_bundle_adjust

K = np.array([[520.0, 0.0, 320.0], [0.0, 520.0, 180.0], [0.0, 0.0, 1.0]])


def _scene(rng: np.random.Generator, n_kf: int = 8, n_pts: int = 300, pose_noise: float = 0.006,
           point_noise: float = 0.03, pixel_noise: float = 0.3) -> tuple[Map, SlamConfig, list]:
    """Forward-translating rig looking at a random cloud, with everything perturbed."""
    cfg = SlamConfig(ba_window=7, map_point_max_reproj_px=50.0)
    world = Map(cfg)
    pts = rng.uniform(-2.0, 2.0, (n_pts, 3)) + np.array([0.0, 0.0, 7.0])
    world.points.add(
        pts + rng.normal(0.0, point_noise, pts.shape),
        np.zeros((n_pts, 32), np.uint8),
        np.zeros((n_pts, 3), np.uint8),
        0,
    )
    truth = []
    for i in range(n_kf):
        T = g.rt_to_T(g.so3_exp(np.array([0.0, 0.03 * i, 0.0])), np.array([0.28 * i, 0.0, 0.0]))
        truth.append(T)
        uv, _ = g.project(K, T, pts)
        uv = uv + rng.normal(0.0, pixel_noise, uv.shape)
        noisy = T.copy()
        if i >= 2:      # the two oldest keyframes are the fixed gauge
            noisy[:3, :3] = T[:3, :3] @ g.so3_exp(rng.normal(0.0, pose_noise, 3))
            noisy[:3, 3] = T[:3, 3] + rng.normal(0.0, pose_noise * 3, 3)
        world.add_keyframe(
            KeyFrame(i, i, i / 30.0, noisy, uv.astype(np.float32), np.zeros(n_pts, np.int32),
                     np.zeros((n_pts, 32), np.uint8), np.arange(n_pts, dtype=np.int32))
        )
    return world, cfg, truth


class TestLocalBundleAdjustment:
    def test_reduces_reprojection_error(self, rng: np.random.Generator) -> None:
        world, cfg, _ = _scene(rng)
        before = world.mean_reprojection_error(K)
        out = local_bundle_adjust(world, K, cfg)
        after = world.mean_reprojection_error(K)
        assert out.ran
        assert out.error_after_px < out.error_before_px
        assert after < before * 0.5
        assert after < 1.5

    def test_improves_pose_accuracy(self, rng: np.random.Generator) -> None:
        world, cfg, truth = _scene(rng)

        def err() -> float:
            return float(
                np.mean([
                    np.linalg.norm(kf.T_cw[:3, 3] - T[:3, 3])
                    for kf, T in zip(world.keyframes, truth)
                ])
            )

        before = err()
        assert local_bundle_adjust(world, K, cfg).ran
        assert err() < before

    def test_analytic_jacobian_matches_finite_differences(self, rng: np.random.Generator) -> None:
        """The whole latency argument rests on the Jacobian being exact."""
        from slam import mapping

        world, cfg, _ = _scene(rng, n_kf=5, n_pts=40)
        captured: dict = {}
        original = mapping.least_squares

        def spy(fun, x0, jac=None, **kw):
            captured.update(fun=fun, jac=jac, x0=x0)
            return original(fun, x0, jac=jac, **kw)

        mapping.least_squares = spy
        try:
            local_bundle_adjust(world, K, cfg)
        finally:
            mapping.least_squares = original

        fun, jac, x0 = captured["fun"], captured["jac"], captured["x0"]
        x = x0 + rng.normal(0.0, 0.01, x0.shape)
        analytic = np.asarray(jac(x).todense())
        numeric = np.zeros_like(analytic)
        h = 1e-6
        for k in range(x.size):
            xp, xm = x.copy(), x.copy()
            xp[k] += h
            xm[k] -= h
            numeric[:, k] = (fun(xp) - fun(xm)) / (2 * h)
        assert np.abs(analytic - numeric).max() < 1e-5

    def test_focal_self_calibration(self, rng: np.random.Generator) -> None:
        world, cfg, _ = _scene(rng, pose_noise=0.0, point_noise=0.02, pixel_noise=0.1)
        wrong = K.copy()
        wrong[0, 0] = wrong[1, 1] = 620.0          # 19% too long
        out = local_bundle_adjust(world, wrong, cfg, free_focal=True)
        assert out.ran
        assert abs(out.focal_px - 520.0) < abs(620.0 - 520.0) * 0.6

    def test_never_writes_back_a_worse_solution(self, rng: np.random.Generator) -> None:
        world, cfg, _ = _scene(rng)
        cfg.ba_max_nfev = 1                        # force a useless solve
        poses = [kf.T_cw.copy() for kf in world.keyframes]
        out = local_bundle_adjust(world, K, cfg)
        if not out.ran:
            for kf, T in zip(world.keyframes, poses):
                assert np.allclose(kf.T_cw, T)


class TestPoseGraph:
    @staticmethod
    def _loop(n: int = 60):
        gt = []
        for i in range(n):
            th = 2 * np.pi * i / n
            gt.append(
                (1.0, g.so3_exp(np.array([0.0, th, 0.0])),
                 np.array([3.0 * np.cos(th), 0.12 * np.sin(3 * th), 3.0 * np.sin(th)]))
            )
        return gt

    def _build(self, drift_rot: float, drift_scale: float, n: int = 60):
        gt = self._loop(n)
        est = [(1.0, gt[0][1].copy(), gt[0][2].copy())]
        edges = []
        R_err = g.so3_exp(np.array([0.0, drift_rot, 0.0]))
        for i in range(n - 1):
            z = g.sim3_compose(g.sim3_inv(gt[i]), gt[i + 1])
            # Odometry between keyframes is SE(3): relative scale is 1 by
            # construction, and the drift shows up as translations of the wrong
            # length, exactly as it does in a real monocular map. The length error
            # compounds with i, which is what makes it *drift* rather than a
            # global scale factor that Sim(3) alignment would silently absorb.
            zd = (1.0, z[1] @ R_err, z[2] * drift_scale ** i)
            est.append(g.sim3_compose(est[-1], zd))
            edges.append(PoseGraphEdge(i, i + 1, zd, 1.0))
        edges.append(PoseGraphEdge(n - 1, 0, g.sim3_compose(g.sim3_inv(gt[n - 1]), gt[0]), 1.0))
        return gt, est, edges

    def _closer(self, iterations: int = 15) -> LoopCloser:
        cfg = SlamConfig(pgo_iterations=iterations)
        return LoopCloser(cfg, Map(cfg), np.eye(3), None, Vocabulary.random(64))

    def test_reduces_injected_drift(self) -> None:
        gt, est, edges = self._build(drift_rot=0.008, drift_scale=1.008)
        gt_pos = np.array([v[2] for v in gt])
        before = np.array([v[2] for v in est])
        out = self._closer()._optimise([(s, R.copy(), t.copy()) for s, R, t in est], edges, fixed=0)
        after = np.array([v[2] for v in out])

        # Distance of the final keyframe from where it should be. The
        # first-to-last *closure* gap is not the right measure here: the loop edge
        # correctly places vertex n-1 one step short of vertex 0, so it is meant
        # to settle at the step length, not at zero.
        end_before = float(np.linalg.norm(before[-1] - gt_pos[-1]))
        end_after = float(np.linalg.norm(after[-1] - gt_pos[-1]))
        ate_before = g.align_trajectories(before, gt_pos)[3]
        ate_after = g.align_trajectories(after, gt_pos)[3]

        assert end_before > 1.0                     # the injected drift is real
        assert end_after < end_before * 0.05
        assert ate_after < ate_before * 0.6

    def test_recovers_scale_drift(self) -> None:
        """Compounding length error is absorbed into the vertex scales.

        Scale *drift* (as opposed to a global scale error) makes the loop spiral
        open, and only a Sim(3) graph can pull it shut: an SE(3) graph has no
        parameter to put the correction in.
        """
        gt, est, edges = self._build(drift_rot=0.0, drift_scale=1.012)
        gt_pos = np.array([v[2] for v in gt])
        before = np.array([v[2] for v in est])
        out = self._closer(25)._optimise(
            [(s, R.copy(), t.copy()) for s, R, t in est], edges, fixed=0
        )
        after = np.array([v[2] for v in out])

        end_before = float(np.linalg.norm(before[-1] - gt_pos[-1]))
        end_after = float(np.linalg.norm(after[-1] - gt_pos[-1]))
        assert end_before > 1.0
        assert end_after < end_before * 0.05
        assert g.align_trajectories(after, gt_pos)[3] < g.align_trajectories(before, gt_pos)[3]
        scales = np.array([v[0] for v in out])
        assert scales.max() / scales.min() > 1.05   # the drift landed in the scales

    def test_fixed_vertex_does_not_move(self) -> None:
        _, est, edges = self._build(0.008, 1.008)
        out = self._closer()._optimise([(s, R.copy(), t.copy()) for s, R, t in est], edges, fixed=0)
        assert out[0][0] == pytest.approx(est[0][0])
        assert np.allclose(out[0][1], est[0][1])
        assert np.allclose(out[0][2], est[0][2])

    def test_consistent_graph_is_a_fixed_point(self) -> None:
        """With no drift injected the optimiser must leave the graph alone."""
        _, est, edges = self._build(0.0, 1.0)
        out = self._closer()._optimise([(s, R.copy(), t.copy()) for s, R, t in est], edges, fixed=0)
        moved = max(float(np.linalg.norm(a[2] - b[2])) for a, b in zip(out, est))
        assert moved < 1e-6
