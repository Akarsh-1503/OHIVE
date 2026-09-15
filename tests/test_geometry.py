"""Lie-group algebra, triangulation and similarity registration."""

from __future__ import annotations

import numpy as np
import pytest

from slam import geometry as g


def _rand_rotvec(rng: np.random.Generator, max_angle: float = 3.0) -> np.ndarray:
    """Rotation vector with |w| < pi so log(exp(w)) == w without wrapping."""
    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    return axis * rng.uniform(1e-9, max_angle)


class TestSE3:
    def test_exp_log_roundtrip(self, rng: np.random.Generator) -> None:
        for _ in range(300):
            xi = np.concatenate([rng.normal(size=3) * 3.0, _rand_rotvec(rng)])
            assert np.allclose(g.se3_log(g.se3_exp(xi)), xi, atol=1e-9)

    def test_tiny_rotation_is_stable(self) -> None:
        """The small-angle branch must not divide by |w|."""
        xi = np.array([1.0, -2.0, 0.5, 1e-12, 0.0, -1e-13])
        assert np.allclose(g.se3_log(g.se3_exp(xi)), xi, atol=1e-9)

    def test_inverse(self, rng: np.random.Generator) -> None:
        T = g.se3_exp(np.concatenate([rng.normal(size=3), _rand_rotvec(rng)]))
        assert np.allclose(T @ g.se3_inv(T), np.eye(4), atol=1e-12)


class TestSim3:
    def test_exp_log_roundtrip(self, rng: np.random.Generator) -> None:
        for _ in range(300):
            xi = np.concatenate(
                [rng.normal(size=3) * 3.0, _rand_rotvec(rng), [rng.normal() * 0.9]]
            )
            s, R, t = g.sim3_exp(xi)
            assert np.allclose(g.sim3_log(s, R, t), xi, atol=1e-8)

    def test_zero_scale_matches_se3(self, rng: np.random.Generator) -> None:
        xi6 = np.concatenate([rng.normal(size=3), _rand_rotvec(rng, 1.0)])
        s, R, t = g.sim3_exp(np.concatenate([xi6, [0.0]]))
        T = g.se3_exp(xi6)
        assert s == pytest.approx(1.0)
        assert np.allclose(R, T[:3, :3], atol=1e-12)
        assert np.allclose(t, T[:3, 3], atol=1e-12)

    def test_compose_inverse(self, rng: np.random.Generator) -> None:
        a = g.sim3_exp(np.concatenate([rng.normal(size=3), _rand_rotvec(rng, 1.0), [0.3]]))
        ident = g.sim3_compose(a, g.sim3_inv(a))
        assert ident[0] == pytest.approx(1.0)
        assert np.allclose(ident[1], np.eye(3), atol=1e-12)
        assert np.allclose(ident[2], 0.0, atol=1e-12)

    def test_adjoint_identity(self, rng: np.random.Generator) -> None:
        """Adj(S) xi == (S Exp(xi) S^-1)^vee, the identity the pose graph relies on."""
        for _ in range(200):
            S = g.sim3_exp(
                np.concatenate([rng.normal(size=3), _rand_rotvec(rng, 1.0), [rng.normal() * 0.3]])
            )
            xi = np.concatenate([rng.normal(size=3), rng.normal(size=3), [rng.normal()]]) * 1e-4
            lhs = g.sim3_compose(g.sim3_compose(S, g.sim3_exp(xi)), g.sim3_inv(S))
            rhs = g.sim3_exp(g.sim3_adj(S) @ xi)
            assert lhs[0] == pytest.approx(rhs[0], abs=1e-12)
            assert np.allclose(lhs[1], rhs[1], atol=1e-12)
            assert np.allclose(lhs[2], rhs[2], atol=1e-12)

    def test_little_adjoint_is_derivative_of_adjoint(self, rng: np.random.Generator) -> None:
        xi = np.concatenate([rng.normal(size=3), _rand_rotvec(rng, 1.0), [rng.normal()]])
        h = 1e-6
        num = (g.sim3_adj(g.sim3_exp(h * xi)) - g.sim3_adj(g.sim3_exp(-h * xi))) / (2 * h)
        assert np.allclose(num, g.sim3_ad(xi), atol=1e-7)


class TestQuaternion:
    def test_roundtrip_is_scalar_first(self, rng: np.random.Generator) -> None:
        R = g.so3_exp(_rand_rotvec(rng, 2.0))
        q = g.quat_from_R(R)
        assert q.shape == (4,)
        assert np.linalg.norm(q) == pytest.approx(1.0)
        assert np.allclose(g.R_from_quat(q), R, atol=1e-12)

    def test_identity_is_1000(self) -> None:
        q = g.quat_from_R(np.eye(3))
        assert np.allclose(np.abs(q), [1.0, 0.0, 0.0, 0.0], atol=1e-12)


class TestTriangulation:
    def test_recovers_known_scene(self, rng: np.random.Generator) -> None:
        K = np.array([[520.0, 0.0, 320.0], [0.0, 520.0, 240.0], [0.0, 0.0, 1.0]])
        pts = rng.uniform(-2.0, 2.0, (150, 3)) + np.array([0.0, 0.0, 6.0])
        T1 = np.eye(4)
        T2 = g.rt_to_T(g.so3_exp(np.array([0.0, -0.08, 0.0])), np.array([-0.9, 0.05, 0.0]))
        uv1, _ = g.project(K, T1, pts)
        uv2, _ = g.project(K, T2, pts)
        out = g.triangulate(
            g.projection_matrix(K, T1), g.projection_matrix(K, T2), uv1, uv2
        )
        assert np.abs(out - pts).max() < 1e-7

    def test_degenerate_point_is_flagged_behind_camera(self) -> None:
        """A zero-baseline pair must not produce inf coordinates."""
        K = np.array([[500.0, 0.0, 320.0], [0.0, 500.0, 240.0], [0.0, 0.0, 1.0]])
        P = g.projection_matrix(K, np.eye(4))
        uv = np.array([[320.0, 240.0]])
        out = g.triangulate(P, P.copy(), uv, uv)
        assert np.isfinite(out).all()
        assert out[0, 2] <= 0.0

    def test_parallax_matches_geometry(self) -> None:
        pts = np.array([[0.0, 0.0, 10.0]])
        ang = g.parallax_deg(pts, np.array([-0.5, 0.0, 0.0]), np.array([0.5, 0.0, 0.0]))
        assert ang[0] == pytest.approx(np.degrees(2 * np.arctan(0.5 / 10.0)), rel=1e-9)


class TestUmeyama:
    def test_recovers_known_similarity(self, rng: np.random.Generator) -> None:
        src = rng.normal(size=(120, 3)) * 2.0
        s_true, R_true = 2.37, g.so3_exp(np.array([0.31, -0.72, 0.18]))
        t_true = np.array([1.5, -2.25, 3.75])
        dst = s_true * (src @ R_true.T) + t_true
        s, R, t = g.umeyama(src, dst)
        assert s == pytest.approx(s_true, rel=1e-12)
        assert np.allclose(R, R_true, atol=1e-12)
        assert np.allclose(t, t_true, atol=1e-11)

    def test_rejects_reflection(self, rng: np.random.Generator) -> None:
        src = rng.normal(size=(60, 3))
        dst = src * np.array([1.0, 1.0, -1.0])      # improper transform
        _, R, _ = g.umeyama(src, dst)
        assert np.linalg.det(R) == pytest.approx(1.0, abs=1e-9)

    def test_ransac_ignores_outliers(self, rng: np.random.Generator) -> None:
        src = rng.normal(size=(200, 3)) * 2.0
        s_true, R_true = 1.8, g.so3_exp(np.array([0.1, 0.4, -0.2]))
        t_true = np.array([0.4, 1.1, -0.7])
        dst = s_true * (src @ R_true.T) + t_true
        bad = rng.choice(200, 70, replace=False)     # 35% gross outliers
        dst[bad] += rng.normal(size=(70, 3)) * 25.0
        out = g.ransac_sim3(src, dst, threshold=0.05, iterations=600, min_inliers=30,
                            rng=np.random.default_rng(0))
        assert out is not None
        s, R, t, mask = out
        assert s == pytest.approx(s_true, rel=1e-6)
        assert np.allclose(R, R_true, atol=1e-6)
        assert mask.sum() >= 120
        assert not mask[bad].any()


class TestTrajectoryAlignment:
    def test_alignment_is_scale_invariant(self, rng: np.random.Generator) -> None:
        gt = np.cumsum(rng.normal(size=(80, 3)), axis=0)
        est = 0.25 * (gt @ g.so3_exp(np.array([0.2, 0.1, -0.3])).T) + np.array([5.0, -1.0, 2.0])
        s, _, _, ate = g.align_trajectories(est, gt)
        assert s == pytest.approx(4.0, rel=1e-9)
        assert ate < 1e-9
