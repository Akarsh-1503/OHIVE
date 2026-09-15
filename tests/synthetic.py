"""Synthetic ground-truth video generator.

Renders a textured room seen by a virtual camera flying a closed loop, encodes
it to a real ``.mp4``, and hands back the exact camera poses. Everything in the
accuracy story (ATE, drift reduction, loop-closure benefit) is measured against
this, because no consumer clip comes with ground truth.

The scene is built from ~800 small textured planar quads tiled over a cylinder
wall, a floor and a ceiling, plus free-floating quads at intermediate depths.
Two reasons for quads rather than a point cloud of discs: a disc has a nearly
rotation-invariant ORB descriptor so every disc matches every other disc, and a
single-depth point shell gives the initialiser no reason to prefer the essential
matrix over a homography. Each quad is rendered with an exact
plane-induced homography, so the imagery is geometrically correct, not faked.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

Array = np.ndarray


@dataclass
class SyntheticSequence:
    path: Path
    poses_wc: Array          # (N, 4, 4) ground-truth world-from-camera
    landmarks: Array         # (M, 3) ground-truth 3D quad corners
    K: Array                 # (3, 3) ground-truth intrinsics at render resolution
    width: int
    height: int
    fps: float

    @property
    def positions(self) -> Array:
        return self.poses_wc[:, :3, 3]

    @property
    def path_length(self) -> float:
        d = np.diff(self.positions, axis=0)
        return float(np.linalg.norm(d, axis=1).sum())


def _look_at(center: Array, forward: Array, world_up: Array) -> Array:
    """Build T_wc with camera axes [right, down, forward] (OpenCV convention)."""
    f = forward / np.linalg.norm(forward)
    d = -(world_up - f * float(np.dot(world_up, f)))
    d /= np.linalg.norm(d)
    r = np.cross(d, f)
    r /= np.linalg.norm(r)
    T = np.eye(4)
    T[:3, 0] = r
    T[:3, 1] = d
    T[:3, 2] = f
    T[:3, 3] = center
    return T


def loop_trajectory(
    n_frames: int,
    radius: float = 1.6,
    wobble: float = 0.09,
    look_angle: float = 0.5,
    seed: int = 0,
) -> Array:
    """Closed circular fly-through around a room.

    ``look_angle`` blends the view direction between radial (0, straight at the
    wall) and tangential (pi/2, down the room). Pure radial puts every visible
    surface at one distance, which is the degenerate planar case for the
    essential matrix; ~50 degrees keeps a healthy depth spread in frame while
    still translating across the optical axis, so parallax stays observable.
    """
    rng = np.random.default_rng(seed)
    phase = rng.uniform(0.0, 2.0 * np.pi, size=3)
    theta = np.linspace(0.0, 2.0 * np.pi, n_frames, endpoint=False)
    poses = np.zeros((n_frames, 4, 4))
    for i, th in enumerate(theta):
        # Non-planar wobble keeps the sequence from degenerating into a pure
        # planar rotation, which is not representative of handheld video.
        bob = wobble * np.sin(3.0 * th + phase[0])
        sway = wobble * np.sin(2.0 * th + phase[1])
        center = np.array(
            [(radius + sway) * np.cos(th), bob, (radius + sway) * np.sin(th)]
        )
        yaw = th + look_angle + 0.18 * np.sin(2.0 * th + phase[2])
        forward = np.array(
            [np.cos(yaw), 0.15 * np.sin(5.0 * th + phase[0]), np.sin(yaw)]
        )
        poses[i] = _look_at(center, forward, np.array([0.0, 1.0, 0.0]))
    return poses


def _texture(rng: np.random.Generator, size: int = 128) -> Array:
    """Band-limited random texture: smooth colour gradients plus a few soft shapes.

    Two earlier attempts were measurably wrong. Pure high-frequency noise aliases
    under scale change and stops repeating after two frames. Hard-edged flat
    shapes look right but are *piecewise constant*, and BRIEF compares intensity
    pairs -- inside a constant region the sign of each comparison is decided by
    sensor noise, so the descriptor of a geometrically correct correspondence
    drifted to a median Hamming distance of 60-80 bits (a good ORB match is
    20-40). Band-limited noise has a gradient everywhere, which is what real
    surfaces look like to ORB.
    """
    coarse = rng.integers(20, 236, (7, 7, 3)).astype(np.float32)
    base = cv2.resize(coarse, (size, size), interpolation=cv2.INTER_CUBIC)
    mid = cv2.resize(
        rng.integers(0, 256, (17, 17, 3)).astype(np.float32),
        (size, size),
        interpolation=cv2.INTER_CUBIC,
    )
    img = np.clip(0.55 * base + 0.45 * mid, 0, 255).astype(np.uint8)
    for _ in range(int(rng.integers(2, 5))):
        colour = tuple(int(c) for c in rng.integers(0, 256, 3))
        p0 = rng.integers(0, size, 2)
        if rng.random() < 0.5:
            cv2.circle(img, tuple(p0), int(rng.integers(8, size // 4)), colour, -1)
        else:
            cv2.rectangle(img, tuple(p0), tuple(rng.integers(0, size, 2)), colour, -1)
    return cv2.GaussianBlur(img, (0, 0), size / 48.0)


def _quad(center: Array, u: Array, v: Array, hw: float, hh: float) -> Array:
    """Four corners in TL, TR, BR, BL order for a plane spanned by u, v."""
    return np.stack(
        [
            center - u * hw - v * hh,
            center + u * hw - v * hh,
            center + u * hw + v * hh,
            center - u * hw + v * hh,
        ]
    )


def _box(center: Array, half: Array, rng: np.random.Generator) -> list[Array]:
    """Four side faces of an axis-aligned box; the caller yaws it afterwards."""
    hx, hy, hz = half
    ex, ey, ez = np.eye(3)
    return [
        _quad(center + ez * hz, ex, ey, hx, hy),
        _quad(center - ez * hz, ex, ey, hx, hy),
        _quad(center + ex * hx, ez, ey, hz, hy),
        _quad(center - ex * hx, ez, ey, hz, hy),
    ]


def build_room(
    rng: np.random.Generator,
    wall_radius: float = 5.0,
    half_height: float = 2.6,
    tile: float = 0.55,
    n_pillars: int = 120,
    n_floaters: int = 70,
    texture_size: int = 128,
) -> tuple[list[Array], list[Array]]:
    """Cylindrical textured room plus free-standing boxes and panels.

    The boxes matter more than they look: a camera that only ever sees one
    distant wall is a *planar, rotation-dominated* scene, which is the textbook
    degeneracy of the essential matrix -- measured on an earlier version of this
    scene, recoverPose returned translation directions 80-90 degrees off truth.
    Real handheld footage of a room always has near clutter at several depths,
    so putting it in is fidelity, not generosity.
    """
    quads: list[Array] = []

    n_ang = max(8, int(round(2.0 * np.pi * wall_radius / tile)))
    n_row = max(2, int(round(2.0 * half_height / tile)))
    up = np.array([0.0, 1.0, 0.0])
    for a in range(n_ang):
        th = 2.0 * np.pi * (a + 0.5) / n_ang
        radial = np.array([np.cos(th), 0.0, np.sin(th)])
        tangent = np.array([-np.sin(th), 0.0, np.cos(th)])
        arc = np.pi * wall_radius / n_ang
        for r in range(n_row):
            y = -half_height + 2.0 * half_height * (r + 0.5) / n_row
            quads.append(
                _quad(radial * wall_radius + up * y, tangent, up, arc, half_height / n_row)
            )

    span = wall_radius
    n_grid = max(4, int(round(2.0 * span / tile)))
    ex, ez = np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])
    for sign in (-1.0, 1.0):
        for i in range(n_grid):
            for j in range(n_grid):
                x = -span + 2.0 * span * (i + 0.5) / n_grid
                z = -span + 2.0 * span * (j + 0.5) / n_grid
                if x * x + z * z > (wall_radius + 0.4) ** 2:
                    continue
                quads.append(
                    _quad(
                        np.array([x, sign * half_height, z]),
                        ex,
                        ez,
                        span / n_grid,
                        span / n_grid,
                    )
                )

    # Boxes between the camera circle and the wall: 0.5-2.4 m from the lens while
    # the wall sits at ~3 m, which is the depth spread the geometry needs.
    for _ in range(n_pillars):
        th = rng.uniform(0.0, 2.0 * np.pi)
        # Two bands, leaving the camera circle (radius ~3) clear.
        rad = rng.uniform(2.3, 4.6)
        c = np.array([rad * np.cos(th), rng.uniform(-1.3, 1.3), rad * np.sin(th)])
        half = np.array([rng.uniform(0.18, 0.38), rng.uniform(0.3, 0.9), rng.uniform(0.18, 0.38)])
        yaw = rng.uniform(0.0, np.pi)
        Ry = np.array(
            [[np.cos(yaw), 0.0, np.sin(yaw)], [0.0, 1.0, 0.0], [-np.sin(yaw), 0.0, np.cos(yaw)]]
        )
        for face in _box(np.zeros(3), half, rng):
            quads.append(face @ Ry.T + c)

    for _ in range(n_floaters):
        th = rng.uniform(0.0, 2.0 * np.pi)
        rad = rng.uniform(2.3, 4.8)
        c = np.array([rad * np.cos(th), rng.uniform(-1.9, 1.9), rad * np.sin(th)])
        tilt = rng.uniform(-0.5, 0.5)
        u = np.array([-np.sin(th + tilt), 0.0, np.cos(th + tilt)])
        v = np.array([-np.sin(tilt) * np.cos(th), np.cos(tilt), -np.sin(tilt) * np.sin(th)])
        v /= np.linalg.norm(v)
        quads.append(_quad(c, u, v, rng.uniform(0.2, 0.45), rng.uniform(0.2, 0.45)))

    textures = [_texture(rng, texture_size) for _ in quads]
    return quads, textures


def render_frame(
    T_cw: Array,
    K: Array,
    width: int,
    height: int,
    quads: list[Array],
    textures: list[Array],
    canvas: Array,
    centers: Array,
    radii: Array,
) -> Array:
    """Painter's-algorithm render of every quad that survives frustum culling.

    Quads straddling the image plane are dropped rather than clipped, so the
    tiling has to be fine enough that the dropped ones are small: on a coarse
    tiling roughly a quarter of the visible surface popped in and out between
    consecutive frames, which by itself halved ORB's feature repeatability.
    """
    canvas[:] = 24
    R, t = T_cw[:3, :3], T_cw[:3, 3]
    cc = centers @ R.T + t
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    depth = cc[:, 2]
    # Cheap cone cull: keep anything whose bounding sphere can touch the frustum.
    margin = radii + 0.05
    ok = depth > margin
    with np.errstate(invalid="ignore", divide="ignore"):
        su = fx * np.abs(cc[:, 0]) / np.maximum(depth, 1e-6)
        sv = fy * np.abs(cc[:, 1]) / np.maximum(depth, 1e-6)
        pad = fx * margin / np.maximum(depth, 1e-6)
    ok &= (su - pad < width) & (sv - pad < height)
    cand = np.flatnonzero(ok)
    order = cand[np.argsort(-depth[cand])]
    ts = textures[0].shape[0]
    src = np.array(
        [[0.0, 0.0], [ts - 1.0, 0.0], [ts - 1.0, ts - 1.0], [0.0, ts - 1.0]],
        dtype=np.float32,
    )
    for qi in order:
        Xc = quads[qi] @ R.T + t
        if np.any(Xc[:, 2] < 0.18):
            continue
        u = fx * Xc[:, 0] / Xc[:, 2] + cx
        v = fy * Xc[:, 1] / Xc[:, 2] + cy
        x0 = max(int(np.floor(u.min())), 0)
        x1 = min(int(np.ceil(u.max())) + 1, width)
        y0 = max(int(np.floor(v.min())), 0)
        y1 = min(int(np.ceil(v.max())) + 1, height)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        dst = np.stack([u - x0, v - y0], axis=1).astype(np.float32)
        try:
            H = cv2.getPerspectiveTransform(src, dst)
        except cv2.error:
            continue
        roi = canvas[y0:y1, x0:x1].copy()
        cv2.warpPerspective(
            textures[qi],
            H,
            (x1 - x0, y1 - y0),
            dst=roi,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_TRANSPARENT,
        )
        canvas[y0:y1, x0:x1] = roi
    return canvas


def render_sequence(
    path: str | Path,
    n_frames: int = 300,
    width: int = 960,
    height: int = 540,
    fps: float = 30.0,
    focal_ratio: float = 0.85,
    radius: float = 1.6,
    look_angle: float = 0.5,
    seed: int = 0,
    noise_sigma: float = 2.0,
    motion_blur: int = 0,
) -> SyntheticSequence:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    poses_wc = loop_trajectory(n_frames, radius=radius, look_angle=look_angle, seed=seed)
    quads, textures = build_room(rng)
    f = focal_ratio * width
    K = np.array([[f, 0.0, width / 2.0], [0.0, f, height / 2.0], [0.0, 0.0, 1.0]])

    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    if not writer.isOpened():
        raise RuntimeError(f"cv2.VideoWriter could not open {path}")

    centers = np.array([q.mean(axis=0) for q in quads])
    radii = np.array([np.linalg.norm(q - c, axis=1).max() for q, c in zip(quads, centers)])
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    noise = rng.normal(0.0, max(noise_sigma, 1e-6), (height, width, 3)).astype(np.float32)
    try:
        for i in range(n_frames):
            img = render_frame(
                np.linalg.inv(poses_wc[i]),
                K,
                width,
                height,
                quads,
                textures,
                canvas,
                centers,
                radii,
            )
            out = img
            if noise_sigma > 0:
                # Rolling the same noise field is far cheaper than resampling and
                # still decorrelates sensor noise between frames.
                out = np.clip(
                    img.astype(np.float32) + np.roll(noise, i * 37, axis=0), 0, 255
                ).astype(np.uint8)
            if motion_blur > 1:
                out = cv2.blur(out, (motion_blur, 1))
            writer.write(out)
    finally:
        writer.release()

    return SyntheticSequence(
        path=path,
        poses_wc=poses_wc,
        landmarks=np.concatenate(quads, axis=0),
        K=K,
        width=width,
        height=height,
        fps=fps,
    )
