"""Lie-group and multi-view geometry primitives.

Conventions (they match the API contract):
  * A pose is stored as ``T_cw`` (camera-from-world): ``x_c = R @ x_w + t``.
  * Poses are reported to callers as ``T_wc = inv(T_cw)``.
  * Sim(3) elements are the triple ``(s, R, t)`` acting as ``x -> s R x + t``.
  * Tangent vectors are ordered ``[v (3), omega (3)]`` for se(3) and
    ``[v (3), omega (3), sigma (1)]`` for sim(3), matching Sophus.

Right (body-frame) perturbations are used throughout: ``T <- T * Exp(delta)``.
"""

from __future__ import annotations

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

_EPS = 1e-9
Array = np.ndarray


# --------------------------------------------------------------------------
# SO(3)
# --------------------------------------------------------------------------
def skew(v: Array) -> Array:
    x, y, z = float(v[0]), float(v[1]), float(v[2])
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype=np.float64)


def so3_exp(w: Array) -> Array:
    return Rotation.from_rotvec(np.asarray(w, dtype=np.float64)).as_matrix()


def so3_log(R: Array) -> Array:
    return Rotation.from_matrix(np.asarray(R, dtype=np.float64)).as_rotvec()


def quat_from_R(R: Array) -> Array:
    """Return (qw, qx, qy, qz); scipy uses scalar-last so we roll the array."""
    q = Rotation.from_matrix(np.asarray(R, dtype=np.float64)).as_quat()
    return np.array([q[3], q[0], q[1], q[2]], dtype=np.float64)


def R_from_quat(q: Array) -> Array:
    qw, qx, qy, qz = (float(x) for x in q)
    return Rotation.from_quat([qx, qy, qz, qw]).as_matrix()


def _left_jacobian_so3(w: Array) -> Array:
    theta = float(np.linalg.norm(w))
    W = skew(w)
    if theta < 1e-7:
        return np.eye(3) + 0.5 * W + (1.0 / 6.0) * (W @ W)
    t2 = theta * theta
    return (
        np.eye(3)
        + ((1.0 - np.cos(theta)) / t2) * W
        + ((theta - np.sin(theta)) / (t2 * theta)) * (W @ W)
    )


# --------------------------------------------------------------------------
# SE(3)
# --------------------------------------------------------------------------
def se3_exp(xi: Array) -> Array:
    xi = np.asarray(xi, dtype=np.float64).reshape(6)
    v, w = xi[:3], xi[3:]
    T = np.eye(4)
    T[:3, :3] = so3_exp(w)
    T[:3, 3] = _left_jacobian_so3(w) @ v
    return T


def se3_log(T: Array) -> Array:
    T = np.asarray(T, dtype=np.float64)
    w = so3_log(T[:3, :3])
    v = np.linalg.solve(_left_jacobian_so3(w), T[:3, 3])
    return np.concatenate([v, w])


def se3_inv(T: Array) -> Array:
    R = T[:3, :3]
    out = np.eye(4)
    out[:3, :3] = R.T
    out[:3, 3] = -R.T @ T[:3, 3]
    return out


def rt_to_T(R: Array, t: Array) -> Array:
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = np.asarray(t, dtype=np.float64).reshape(3)
    return T


# --------------------------------------------------------------------------
# Sim(3)
# --------------------------------------------------------------------------
def sim3_exp(xi: Array) -> tuple[float, Array, Array]:
    """Sophus-compatible Sim(3) exponential for xi = [v, omega, sigma]."""
    xi = np.asarray(xi, dtype=np.float64).reshape(7)
    v, w, sigma = xi[:3], xi[3:6], float(xi[6])
    theta = float(np.linalg.norm(w))
    s = float(np.exp(sigma))
    R = so3_exp(w)
    W = _sim3_W(sigma, theta, s)
    Om = skew(w)
    M = W[0] * Om + W[1] * (Om @ Om) + W[2] * np.eye(3)
    return s, R, M @ v


def _sim3_W(sigma: float, theta: float, s: float) -> tuple[float, float, float]:
    """Coefficients (A, B, C) of the Sim(3) translation map W = A*Om + B*Om^2 + C*I."""
    t2 = theta * theta
    if abs(sigma) < 1e-7:
        C = 1.0
        if theta < 1e-7:
            A, B = 0.5, 1.0 / 6.0
        else:
            A = (1.0 - np.cos(theta)) / t2
            B = (theta - np.sin(theta)) / (t2 * theta)
    else:
        C = (s - 1.0) / sigma
        s2 = sigma * sigma
        if theta < 1e-7:
            A = ((sigma - 1.0) * s + 1.0) / s2
            B = (s * 0.5 * s2 + s - 1.0 - sigma * s) / (s2 * sigma)
        else:
            a = s * np.sin(theta)
            b = s * np.cos(theta)
            c = t2 + s2
            A = (a * sigma + (1.0 - b) * theta) / (theta * c)
            B = (C - ((b - 1.0) * sigma + a * theta) / c) / t2
    return float(A), float(B), float(C)


def sim3_log(s: float, R: Array, t: Array) -> Array:
    sigma = float(np.log(s))
    w = so3_log(R)
    theta = float(np.linalg.norm(w))
    A, B, C = _sim3_W(sigma, theta, float(s))
    Om = skew(w)
    M = A * Om + B * (Om @ Om) + C * np.eye(3)
    v = np.linalg.solve(M, np.asarray(t, dtype=np.float64).reshape(3))
    return np.concatenate([v, w, [sigma]])


def sim3_compose(
    a: tuple[float, Array, Array], b: tuple[float, Array, Array]
) -> tuple[float, Array, Array]:
    sa, Ra, ta = a
    sb, Rb, tb = b
    return sa * sb, Ra @ Rb, sa * (Ra @ tb) + ta


def sim3_inv(a: tuple[float, Array, Array]) -> tuple[float, Array, Array]:
    s, R, t = a
    Ri = R.T
    return 1.0 / s, Ri, -(Ri @ t) / s


def sim3_adj(a: tuple[float, Array, Array]) -> Array:
    """Adjoint of Sim(3): Adj(S) xi = (S Exp(xi) S^-1)^vee.

    Derived from S xi^ S^-1 with S = [[sR, t], [0, 1]]:
        v' = s R v + [t]_x R w - sigma t,  w' = R w,  sigma' = sigma.
    """
    s, R, t = a
    A = np.zeros((7, 7))
    A[:3, :3] = s * R
    A[:3, 3:6] = skew(t) @ R
    A[:3, 6] = -np.asarray(t, dtype=np.float64).reshape(3)
    A[3:6, 3:6] = R
    A[6, 6] = 1.0
    return A


def sim3_ad(xi: Array) -> Array:
    """Little adjoint ad(xi) = d/dt Adj(Exp(t xi)) at t = 0."""
    xi = np.asarray(xi, dtype=np.float64).reshape(7)
    v, w, sigma = xi[:3], xi[3:6], float(xi[6])
    A = np.zeros((7, 7))
    A[:3, :3] = sigma * np.eye(3) + skew(w)
    A[:3, 3:6] = skew(v)
    A[:3, 6] = -v
    A[3:6, 3:6] = skew(w)
    return A


def sim3_from_T(T: Array, s: float = 1.0) -> tuple[float, Array, Array]:
    return float(s), np.asarray(T[:3, :3], dtype=np.float64).copy(), np.asarray(
        T[:3, 3], dtype=np.float64
    ).copy()


def sim3_right_jinv(r: Array) -> Array:
    """Second-order approximation of the inverse right Jacobian, I + 0.5 ad(r).

    Exact to O(|r|^2); pose-graph residuals are small near the solution so this
    costs nothing in accuracy while avoiding the closed-form 7x7 series.
    """
    return np.eye(7) + 0.5 * sim3_ad(r)


# --------------------------------------------------------------------------
# Two-view geometry
# --------------------------------------------------------------------------
def projection_matrix(K: Array, T_cw: Array) -> Array:
    return K @ T_cw[:3, :4]


def triangulate(P1: Array, P2: Array, pts1: Array, pts2: Array) -> Array:
    """Linear triangulation of matched pixel observations -> (N, 3) world points."""
    if len(pts1) == 0:
        return np.zeros((0, 3))
    X = cv2.triangulatePoints(
        P1.astype(np.float64),
        P2.astype(np.float64),
        np.ascontiguousarray(pts1.T.astype(np.float64)),
        np.ascontiguousarray(pts2.T.astype(np.float64)),
    )
    w = X[3]
    # Points at infinity produce w ~ 0; mark them as behind the camera so the
    # cheirality filter drops them instead of producing +-inf coordinates.
    bad = np.abs(w) < 1e-12
    w = np.where(bad, 1.0, w)
    out = (X[:3] / w).T
    out[bad] = np.array([0.0, 0.0, -1.0])
    return out


def parallax_deg(pts_w: Array, c1: Array, c2: Array) -> Array:
    """Angle at each 3D point between the rays from the two camera centres."""
    r1 = pts_w - c1.reshape(1, 3)
    r2 = pts_w - c2.reshape(1, 3)
    n1 = np.linalg.norm(r1, axis=1)
    n2 = np.linalg.norm(r2, axis=1)
    denom = np.maximum(n1 * n2, _EPS)
    cos = np.clip(np.einsum("ij,ij->i", r1, r2) / denom, -1.0, 1.0)
    return np.degrees(np.arccos(cos))


def project(K: Array, T_cw: Array, pts_w: Array) -> tuple[Array, Array]:
    """Project world points. Returns (uv, depth); callers must gate on depth > 0."""
    Xc = pts_w @ T_cw[:3, :3].T + T_cw[:3, 3]
    z = Xc[:, 2]
    safe = np.where(np.abs(z) < 1e-9, 1e-9, z)
    u = K[0, 0] * (Xc[:, 0] / safe) + K[0, 2]
    v = K[1, 1] * (Xc[:, 1] / safe) + K[1, 2]
    return np.stack([u, v], axis=1), z


def reprojection_error(K: Array, T_cw: Array, pts_w: Array, uv: Array) -> Array:
    proj, _ = project(K, T_cw, pts_w)
    return np.linalg.norm(proj - uv, axis=1)


# --------------------------------------------------------------------------
# Similarity registration
# --------------------------------------------------------------------------
def umeyama(src: Array, dst: Array, with_scale: bool = True) -> tuple[float, Array, Array]:
    """Least-squares similarity transform with dst ~= s R src + t (Umeyama 1991)."""
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n = src.shape[0]
    mu_s = src.mean(axis=0)
    mu_d = dst.mean(axis=0)
    xs = src - mu_s
    xd = dst - mu_d
    cov = (xd.T @ xs) / n
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1.0     # reflection guard: keep R in SO(3)
    R = U @ S @ Vt
    if with_scale:
        var_s = float((xs ** 2).sum() / n)
        s = float(np.trace(np.diag(D) @ S) / max(var_s, _EPS))
    else:
        s = 1.0
    t = mu_d - s * (R @ mu_s)
    return s, R, t


def ransac_sim3(
    src: Array,
    dst: Array,
    threshold: float,
    iterations: int = 240,
    min_inliers: int = 12,
    rng: np.random.Generator | None = None,
) -> tuple[float, Array, Array, Array] | None:
    """RANSAC Umeyama over 3D-3D correspondences. Returns (s, R, t, inlier_mask)."""
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n = src.shape[0]
    if n < 3:
        return None
    rng = rng or np.random.default_rng(0)
    thr2 = threshold * threshold
    best_mask: Array | None = None
    best_count = 0
    samples = rng.integers(0, n, size=(iterations, 3))
    for i in range(iterations):
        idx = samples[i]
        if len(np.unique(idx)) < 3:
            continue
        try:
            s, R, t = umeyama(src[idx], dst[idx])
        except np.linalg.LinAlgError:
            continue
        if not np.isfinite(s) or s <= 1e-6 or s > 1e6:
            continue
        resid = dst - (s * (src @ R.T) + t)
        d2 = np.einsum("ij,ij->i", resid, resid)
        mask = d2 < thr2
        count = int(mask.sum())
        if count > best_count:
            best_count = count
            best_mask = mask
    if best_mask is None or best_count < min_inliers:
        return None
    # Refit on the consensus set, then re-classify once.
    s, R, t = umeyama(src[best_mask], dst[best_mask])
    resid = dst - (s * (src @ R.T) + t)
    mask = np.einsum("ij,ij->i", resid, resid) < thr2
    if int(mask.sum()) < min_inliers:
        return None
    s, R, t = umeyama(src[mask], dst[mask])
    return s, R, t, mask


def align_trajectories(est: Array, gt: Array) -> tuple[float, Array, Array, float]:
    """Sim(3)-align an estimated trajectory to ground truth; returns (s, R, t, ate_rmse)."""
    s, R, t = umeyama(est, gt, with_scale=True)
    aligned = s * (est @ R.T) + t
    err = np.linalg.norm(aligned - gt, axis=1)
    return s, R, t, float(np.sqrt((err ** 2).mean()))
