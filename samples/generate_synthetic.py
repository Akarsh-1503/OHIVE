#!/usr/bin/env python3
"""Render the Driftless synthetic demo clips and their ground truth.

Two scenes, both 10 s @ 30 fps, 1280x720, H.264 crf 23:

  synthetic_loop.mp4       closed elliptical trajectory inside a textured room; the last
                           frame returns to the first pose, so loop closure has something
                           real to find.
  synthetic_corridor.mp4   straight forward translation down a corridor; never revisits a
                           viewpoint, so drift accumulates with nothing to correct it.

Rendering is an exact inverse texture mapping: every pixel inside a plane's projected
silhouette is back-projected, intersected with the plane, and sampled from that plane's
texture, with a z-buffer for occlusion. That is slower than warping four corners with a
homography but it clips correctly against the near plane and it gives real, stable
corners — which is the whole point, since ORB has to find them.

Alongside each clip:
  <name>_gt.json      intrinsics + per-frame camera pose (position + quaternion, T_wc)
  <name>_gt_tum.txt   the same trajectory in TUM format, for evo/ATE tooling
  <name>_gt.ply       the ground-truth point cloud, sampled from the scene surfaces

Usage:
    python generate_synthetic.py                  # both clips + manifest.json
    python generate_synthetic.py --scene loop
    python generate_synthetic.py --seconds 4      # quick smoke render
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent

WIDTH, HEIGHT = 1280, 720
FPS = 30.0
SECONDS = 10.0
# ~70.9 deg horizontal FOV: a typical phone main camera, and wide enough that a 7 m room
# fills the frame without the near plane clipping constantly.
FX = FY = 900.0
CX, CY = WIDTH / 2.0, HEIGHT / 2.0
ZNEAR = 0.05

K = np.array([[FX, 0.0, CX], [0.0, FY, CY], [0.0, 0.0, 1.0]], dtype=np.float64)

TEX_PX_PER_M = 280.0
MAX_TEX = 1800
GT_POINTS = 55_000


# --------------------------------------------------------------------------------------
# Procedural textures
# --------------------------------------------------------------------------------------

def _noise(rng: np.random.Generator, h: int, w: int, scale: int) -> np.ndarray:
    small = rng.random((max(h // scale, 2), max(w // scale, 2))).astype(np.float32)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)


def _speckle(rng: np.random.Generator, img: np.ndarray, count: int, size: int,
             strength: float) -> None:
    """Scatter small rectangles. This is what guarantees ORB has corners everywhere;
    smooth gradient-only textures produce a detector-starved scene."""
    h, w = img.shape[:2]
    xs = rng.integers(0, max(w - size, 1), count)
    ys = rng.integers(0, max(h - size, 1), count)
    ws = rng.integers(max(size // 3, 1), size + 1, count)
    hs = rng.integers(max(size // 3, 1), size + 1, count)
    # Mostly luminance, a little chroma: scuffs and dirt, not confetti. ORB works on the
    # grey channel anyway, so the corner yield is unchanged.
    tints = (rng.normal(0.0, strength, (count, 1))
             + rng.normal(0.0, strength * 0.22, (count, 3))).astype(np.float32)
    for x, y, bw, bh, tint in zip(xs, ys, ws, hs, tints, strict=True):
        patch = img[y:y + bh, x:x + bw]
        patch += tint


def _base(rng: np.random.Generator, h: int, w: int, colour: tuple[float, float, float],
          rough: float) -> np.ndarray:
    img = np.empty((h, w, 3), np.float32)
    img[:] = np.asarray(colour, np.float32)
    img += (_noise(rng, h, w, 4)[:, :, None] - 0.5) * (rough * 2.0)
    img += (_noise(rng, h, w, 24)[:, :, None] - 0.5) * (rough * 3.0)
    img += rng.normal(0.0, rough * 0.45, (h, w, 3)).astype(np.float32)
    return img


def _grid(img: np.ndarray, step: int, colour: tuple[float, float, float], thick: int) -> None:
    h, w = img.shape[:2]
    c = np.asarray(colour, np.float32)
    for y in range(0, h, step):
        img[y:y + thick, :] = c
    for x in range(0, w, step):
        img[:, x:x + thick] = c


def _panel(rng: np.random.Generator, img: np.ndarray, x: int, y: int, w: int, h: int) -> None:
    """A poster / whiteboard / framed print: a bordered rectangle of blocky content."""
    h_img, w_img = img.shape[:2]
    x, y = max(x, 0), max(y, 0)
    w, h = min(w, w_img - x), min(h, h_img - y)
    if w < 20 or h < 20:
        return
    bg = (rng.uniform(60, 235) + rng.uniform(-12, 12, 3)).astype(np.float32)
    if rng.random() < 0.3:
        bg = rng.uniform(45, 235, 3).astype(np.float32)
    img[y:y + h, x:x + w] = bg
    img[y:y + h, x:x + 4] = bg * 0.35
    img[y:y + h, x + w - 4:x + w] = bg * 0.35
    img[y:y + 4, x:x + w] = bg * 0.35
    img[y + h - 4:y + h, x:x + w] = bg * 0.35
    style = rng.integers(0, 3)
    if style == 0:                                        # text-like rules
        yy = y + int(h * 0.12)
        while yy < y + h - 12:
            lw = int(rng.uniform(0.25, 0.88) * (w - 24))
            img[yy:yy + max(3, h // 40), x + 12:x + 12 + lw] = bg * rng.uniform(0.15, 0.45)
            yy += max(int(h * 0.075), 8)
    elif style == 1:                                      # colour blocks
        for _ in range(int(rng.integers(6, 16))):
            bw = int(rng.integers(8, max(int(w * 0.35), 10)))
            bh = int(rng.integers(8, max(int(h * 0.35), 10)))
            bx = int(rng.integers(x + 6, max(x + w - bw - 6, x + 7)))
            by = int(rng.integers(y + 6, max(y + h - bh - 6, y + 7)))
            img[by:by + bh, bx:bx + bw] = (rng.uniform(25, 240)
                                           + rng.uniform(-30, 30, 3)).astype(np.float32)
    else:                                                 # checker
        cell = max(int(min(w, h) / rng.integers(5, 11)), 6)
        dark = bg * 0.3
        for j, yy in enumerate(range(y + 6, y + h - 6, cell)):
            for i, xx in enumerate(range(x + 6, x + w - 6, cell)):
                if (i + j) % 2 == 0:
                    img[yy:yy + cell, xx:xx + cell] = dark


def tex_wall(rng: np.random.Generator, h: int, w: int) -> np.ndarray:
    img = _base(rng, h, w, (rng.uniform(150, 200),) * 3, 9.0)
    img[:, :, 0] += rng.uniform(-14, 16)
    img[:, :, 2] += rng.uniform(-16, 14)
    _grid(img, max(h // 3, 40), (128.0, 126.0, 124.0), 3)       # panel joints
    for _ in range(int(rng.integers(3, 7))):
        pw = int(rng.uniform(0.12, 0.26) * w)
        ph = int(rng.uniform(0.18, 0.42) * h)
        _panel(rng, img, int(rng.uniform(0, w - pw)), int(rng.uniform(0, h - ph)), pw, ph)
    _speckle(rng, img, 2600, max(w // 90, 6), 16.0)
    return np.clip(img, 0, 255)


def tex_floor(rng: np.random.Generator, h: int, w: int) -> np.ndarray:
    img = _base(rng, h, w, (78.0, 96.0, 124.0), 11.0)
    plank = max(h // 16, 24)
    for y in range(0, h, plank):
        img[y:y + plank] *= rng.uniform(0.86, 1.16)
        img[y:y + 3] *= 0.55
        for x in range(int(rng.integers(0, plank)), w, int(rng.uniform(3.0, 6.0) * plank)):
            img[y:y + plank, x:x + 3] *= 0.6
    img += (_noise(rng, h, w, 2)[:, :, None] - 0.5) * 22.0
    _speckle(rng, img, 3200, max(w // 110, 5), 13.0)
    return np.clip(img, 0, 255)


def tex_ceiling(rng: np.random.Generator, h: int, w: int) -> np.ndarray:
    img = _base(rng, h, w, (198.0, 200.0, 201.0), 6.0)
    _grid(img, max(min(h, w) // 6, 40), (150.0, 152.0, 154.0), 5)
    _speckle(rng, img, 1800, max(w // 110, 5), 11.0)
    return np.clip(img, 0, 255)


def tex_crate(rng: np.random.Generator, h: int, w: int) -> np.ndarray:
    img = _base(rng, h, w, (118.0, 152.0, 186.0), 10.0)
    img[:, int(w * 0.44):int(w * 0.56)] *= 0.78                  # packing tape
    _panel(rng, img, int(w * 0.12), int(h * 0.18), int(w * 0.34), int(h * 0.36))
    _panel(rng, img, int(w * 0.58), int(h * 0.52), int(w * 0.32), int(h * 0.3))
    _speckle(rng, img, 1500, max(w // 60, 5), 15.0)
    return np.clip(img, 0, 255)


def tex_screen(rng: np.random.Generator, h: int, w: int) -> np.ndarray:
    img = np.full((h, w, 3), 32.0, np.float32)
    for _ in range(int(rng.integers(18, 34))):
        bx, by = int(rng.uniform(0, w * 0.85)), int(rng.uniform(0, h * 0.9))
        bw, bh = int(rng.uniform(w * 0.06, w * 0.4)), int(rng.uniform(h * 0.03, h * 0.16))
        img[by:by + bh, bx:bx + bw] = (rng.uniform(45, 235)
                                       + rng.uniform(-24, 24, 3)).astype(np.float32)
    _speckle(rng, img, 900, max(w // 50, 4), 20.0)
    return np.clip(img, 0, 255)


TEXTURES = {"wall": tex_wall, "floor": tex_floor, "ceiling": tex_ceiling,
            "crate": tex_crate, "screen": tex_screen}


# --------------------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------------------

@dataclass
class Plane:
    """A textured parallelogram: point = origin + a*u + b*v with a, b in [0, 1]."""
    origin: np.ndarray
    u: np.ndarray
    v: np.ndarray
    tex: np.ndarray
    shade: float

    @property
    def corners(self) -> np.ndarray:
        o, u, v = self.origin, self.u, self.v
        return np.stack([o, o + u, o + u + v, o + v])

    @property
    def area(self) -> float:
        return float(np.linalg.norm(np.cross(self.u, self.v)))

    @property
    def normal(self) -> np.ndarray:
        n = np.cross(self.u, self.v)
        return n / (np.linalg.norm(n) + 1e-12)


def make_plane(rng: np.random.Generator, origin, u, v, kind: str,
               light: np.ndarray) -> Plane:
    o, u, v = (np.asarray(a, np.float64) for a in (origin, u, v))
    tw = int(np.clip(np.linalg.norm(u) * TEX_PX_PER_M, 64, MAX_TEX))
    th = int(np.clip(np.linalg.norm(v) * TEX_PX_PER_M, 64, MAX_TEX))
    tex = TEXTURES[kind](rng, th, tw)
    n = np.cross(u, v)
    n = n / (np.linalg.norm(n) + 1e-12)
    shade = 0.62 + 0.38 * abs(float(np.dot(n, light)))
    return Plane(o, u, v, tex, shade)


def box(rng: np.random.Generator, centre, size, kind: str, light: np.ndarray) -> list[Plane]:
    """Five faces of an axis-aligned box (the bottom face is never visible)."""
    cx, cy, cz = centre
    sx, sy, sz = size
    x0, y0, z0 = cx - sx / 2, cy - sy / 2, cz - sz / 2
    faces = [
        ((x0, y0, z0), (sx, 0, 0), (0, sy, 0)),          # -z
        ((x0 + sx, y0, z0 + sz), (-sx, 0, 0), (0, sy, 0)),  # +z
        ((x0, y0, z0 + sz), (0, 0, -sz), (0, sy, 0)),    # -x
        ((x0 + sx, y0, z0), (0, 0, sz), (0, sy, 0)),     # +x
        ((x0, y0, z0), (sx, 0, 0), (0, 0, sz)),          # top (y0 is the smaller y = up)
    ]
    return [make_plane(rng, o, u, v, kind, light) for o, u, v in faces]


def room_scene(rng: np.random.Generator) -> list[Plane]:
    """7 m x 7 m room, 3 m tall, camera eye height at y = 0 (floor y = +1.5).

    Deliberately larger than the camera loop so that each frame holds both near clutter
    (~1 m) and a far wall (~5 m); a narrow depth range would make triangulation
    ill-conditioned and the drift numbers meaningless.
    """
    light = np.array([0.35, -0.86, 0.37])
    light /= np.linalg.norm(light)
    hx = hz = 3.5
    fy, cy_ = 1.5, -1.5
    planes = [
        make_plane(rng, (-hx, cy_, hz), (2 * hx, 0, 0), (0, fy - cy_, 0), "wall", light),
        make_plane(rng, (hx, cy_, -hz), (-2 * hx, 0, 0), (0, fy - cy_, 0), "wall", light),
        make_plane(rng, (-hx, cy_, -hz), (0, 0, 2 * hz), (0, fy - cy_, 0), "wall", light),
        make_plane(rng, (hx, cy_, hz), (0, 0, -2 * hz), (0, fy - cy_, 0), "wall", light),
        make_plane(rng, (-hx, fy, -hz), (2 * hx, 0, 0), (0, 0, 2 * hz), "floor", light),
        make_plane(rng, (-hx, cy_, hz), (2 * hx, 0, 0), (0, 0, -2 * hz), "ceiling", light),
    ]
    # Crates, desks and a freestanding partition: a bare box room is nearly degenerate
    # for monocular SLAM because every feature sits at the same depth.
    for centre, size in [((-2.5, 1.05, -2.2), (0.9, 0.9, 0.9)),
                         ((2.4, 0.95, 2.3), (1.1, 1.1, 0.9)),
                         ((-2.2, 1.2, 2.6), (0.6, 0.6, 1.2)),
                         ((2.7, 1.15, -2.6), (0.7, 0.7, 0.7)),
                         ((0.3, 1.35, 2.9), (1.8, 0.3, 0.8)),
                         ((-2.9, 1.3, 0.4), (0.5, 0.4, 1.9)),
                         ((0.0, 0.45, -2.4), (1.4, 2.1, 0.18)),
                         ((2.6, 0.6, 0.2), (0.2, 1.8, 1.5))]:
        planes += box(rng, centre, size, "crate", light)
    # Wall-mounted screens, inset slightly so they never z-fight with the wall.
    planes += [
        make_plane(rng, (-1.1, -0.55, hz - 0.012), (2.2, 0, 0), (0, 1.1, 0), "screen", light),
        make_plane(rng, (hx - 0.012, -0.5, 1.4), (0, 0, -2.0), (0, 1.1, 0), "screen", light),
        make_plane(rng, (-hx + 0.012, -0.6, -1.4), (0, 0, 1.9), (0, 1.0, 0), "screen", light),
        make_plane(rng, (1.4, -0.5, -hz + 0.012), (-2.0, 0, 0), (0, 1.0, 0), "screen", light),
    ]
    return planes


def corridor_scene(rng: np.random.Generator) -> list[Plane]:
    """2.6 m wide, 3 m tall, 18 m long corridor running along +Z."""
    light = np.array([0.2, -0.9, 0.4])
    light /= np.linalg.norm(light)
    hx, length = 1.3, 18.0
    fy, cy_ = 1.5, -1.5
    planes: list[Plane] = []
    # Split the long walls into 3 m sections so each gets its own texture and the
    # texture resolution stays sane.
    for z0 in np.arange(0.0, length, 3.0):
        planes.append(make_plane(rng, (-hx, cy_, z0), (0, 0, 3.0), (0, fy - cy_, 0), "wall", light))
        planes.append(make_plane(rng, (hx, cy_, z0 + 3.0), (0, 0, -3.0), (0, fy - cy_, 0), "wall", light))
        planes.append(make_plane(rng, (-hx, fy, z0), (2 * hx, 0, 0), (0, 0, 3.0), "floor", light))
        planes.append(make_plane(rng, (-hx, cy_, z0 + 3.0), (2 * hx, 0, 0), (0, 0, -3.0), "ceiling", light))
    planes.append(make_plane(rng, (-hx, cy_, length), (2 * hx, 0, 0), (0, fy - cy_, 0), "wall", light))
    for z in (2.4, 5.6, 8.8, 12.2, 15.1):
        side = 1 if rng.random() < 0.5 else -1
        x = side * (hx - 0.012)
        u = (0, 0, -0.95 * side)
        planes.append(make_plane(rng, (x, -0.35, z), u, (0, 1.1, 0), "screen", light))
    for z, x in ((3.6, -0.85), (7.2, 0.9), (11.4, -0.8), (14.6, 0.85)):
        planes += box(rng, (x, 1.15, z), (0.7, 0.7, 0.7), "crate", light)
    return planes


# --------------------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------------------

def look_at(centre: np.ndarray, target: np.ndarray, roll: float) -> np.ndarray:
    """World-from-camera rotation for an OpenCV camera (+Z forward, +X right, +Y down)."""
    z = target - centre
    z /= np.linalg.norm(z)
    down = np.array([0.0, 1.0, 0.0])
    if abs(float(np.dot(down, z))) > 0.995:
        down = np.array([0.0, 0.0, 1.0])
    x = np.cross(down, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    if roll:
        c, s = math.cos(roll), math.sin(roll)
        x, y = c * x + s * y, -s * x + c * y
    return np.stack([x, y, z], axis=1)


def mat_to_quat(R: np.ndarray) -> tuple[float, float, float, float]:
    """(qw, qx, qy, qz) from a rotation matrix, via the numerically stable branch."""
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        qw, qx, qy, qz = 0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2.0
        qw, qx, qy, qz = (R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2.0
        qw, qx, qy, qz = (R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2.0
        qw, qx, qy, qz = (R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s
    n = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    return qw / n, qx / n, qy / n, qz / n


def _periodic_jitter(phase: np.ndarray, rng: np.random.Generator, amp: float,
                     harmonics: tuple[int, ...] = (2, 3, 5, 7)) -> np.ndarray:
    """Handheld wobble built from integer harmonics of the loop phase, so the trajectory
    closes exactly rather than almost."""
    out = np.zeros_like(phase)
    for k in harmonics:
        out += (amp / k) * np.sin(k * phase + rng.uniform(0, 2 * math.pi))
    return out


def loop_trajectory(n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    phase = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    a, b = 1.6, 1.2
    cx = a * np.cos(phase) + _periodic_jitter(phase, rng, 0.04)
    cz = b * np.sin(phase) + _periodic_jitter(phase, rng, 0.04)
    cy = _periodic_jitter(phase, rng, 0.05, (2, 3, 5)) + 0.03 * np.sin(3 * phase)
    centres = np.stack([cx, cy, cz], axis=1)
    # Aim nearly tangentially rather than straight at the nearest wall: the view then runs
    # across the room, so each frame holds both near clutter and a far wall, which is where
    # the depth range (and so the parallax) comes from.
    lead = 1.55
    tx = 1.9 * np.cos(phase + lead)
    tz = 1.9 * np.sin(phase + lead)
    ty = _periodic_jitter(phase, rng, 0.12, (2, 3))
    targets = np.stack([tx, ty, tz], axis=1)
    rolls = _periodic_jitter(phase, rng, 0.035, (2, 3, 5))
    Rs = np.stack([look_at(centres[i], targets[i], float(rolls[i])) for i in range(n)])
    return centres, Rs


def corridor_trajectory(n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    t = np.linspace(0.0, 1.0, n)
    z = 1.0 + 9.6 * t
    # A slow, smooth yaw drift plus walking bob: no revisited viewpoint anywhere.
    x = 0.16 * np.sin(2.1 * math.pi * t) + 0.10 * np.sin(5.3 * math.pi * t + 0.7)
    y = 0.035 * np.sin(11.0 * math.pi * t) + 0.02 * np.sin(6.0 * math.pi * t + 1.2)
    centres = np.stack([x, y, z], axis=1)
    yaw = 0.10 * np.sin(1.7 * math.pi * t + 0.4)
    pitch = 0.05 * np.sin(2.6 * math.pi * t)
    targets = centres + np.stack([np.sin(yaw) * 3.0, np.sin(pitch) * 3.0,
                                  np.cos(yaw) * 3.0], axis=1)
    rolls = 0.03 * np.sin(4.0 * math.pi * t + 0.9)
    Rs = np.stack([look_at(centres[i], targets[i], float(rolls[i])) for i in range(n)])
    return centres, Rs


# --------------------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------------------

def clip_near(poly: np.ndarray) -> np.ndarray:
    """Sutherland-Hodgman clip of a camera-space polygon against z >= ZNEAR."""
    out: list[np.ndarray] = []
    n = len(poly)
    for i in range(n):
        cur, nxt = poly[i], poly[(i + 1) % n]
        cin, nin = cur[2] >= ZNEAR, nxt[2] >= ZNEAR
        if cin:
            out.append(cur)
        if cin != nin:
            t = (ZNEAR - cur[2]) / (nxt[2] - cur[2])
            out.append(cur + t * (nxt - cur))
    return np.asarray(out) if out else np.empty((0, 3))


class Renderer:
    def __init__(self, planes: list[Plane]) -> None:
        self.planes = planes
        uu, vv = np.meshgrid(np.arange(WIDTH, dtype=np.float32),
                             np.arange(HEIGHT, dtype=np.float32))
        # Ray directions in camera space, normalised so that z == 1.
        self.dir_x = (uu - CX) / FX
        self.dir_y = (vv - CY) / FY

    def render(self, centre: np.ndarray, R_wc: np.ndarray,
               rng: np.random.Generator) -> np.ndarray:
        R_cw = R_wc.T
        colour = np.zeros((HEIGHT, WIDTH, 3), np.float32)
        depth = np.full((HEIGHT, WIDTH), np.inf, np.float32)

        for p in self.planes:
            cam = (p.corners - centre) @ R_cw.T
            poly = clip_near(cam)
            if len(poly) < 3:
                continue
            xy = np.stack([poly[:, 0] / poly[:, 2] * FX + CX,
                           poly[:, 1] / poly[:, 2] * FY + CY], axis=1)
            x0 = max(int(math.floor(xy[:, 0].min())), 0)
            x1 = min(int(math.ceil(xy[:, 0].max())) + 1, WIDTH)
            y0 = max(int(math.floor(xy[:, 1].min())), 0)
            y1 = min(int(math.ceil(xy[:, 1].max())) + 1, HEIGHT)
            if x1 <= x0 or y1 <= y0:
                continue

            o_c = (p.origin - centre) @ R_cw.T
            u_c, v_c = R_cw @ p.u, R_cw @ p.v
            n_c = np.cross(u_c, v_c)
            nn = np.linalg.norm(n_c)
            if nn < 1e-12:
                continue
            n_c = n_c / nn

            dx = self.dir_x[y0:y1, x0:x1]
            dy = self.dir_y[y0:y1, x0:x1]
            denom = n_c[0] * dx + n_c[1] * dy + n_c[2]
            d0 = float(np.dot(n_c, o_c))
            with np.errstate(divide="ignore", invalid="ignore"):
                t = d0 / denom
            valid = np.isfinite(t) & (t > ZNEAR) & (np.abs(denom) > 1e-9)
            if not valid.any():
                continue

            wx = t * dx - o_c[0]
            wy = t * dy - o_c[1]
            wz = t - o_c[2]
            a = (wx * u_c[0] + wy * u_c[1] + wz * u_c[2]) / float(u_c @ u_c)
            b = (wx * v_c[0] + wy * v_c[1] + wz * v_c[2]) / float(v_c @ v_c)
            valid &= (a >= 0.0) & (a <= 1.0) & (b >= 0.0) & (b <= 1.0)
            valid &= t < depth[y0:y1, x0:x1]
            if not valid.any():
                continue

            th, tw = p.tex.shape[:2]
            map_x = np.clip(a * (tw - 1), 0, tw - 1).astype(np.float32)
            map_y = np.clip(b * (th - 1), 0, th - 1).astype(np.float32)
            sampled = cv2.remap(p.tex, map_x, map_y, cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT)
            # Inverse-square-ish falloff keeps distant surfaces from looking painted on.
            fall = (1.0 / (1.0 + 0.035 * np.square(t)))[:, :, None]
            shaded = sampled * (p.shade * (0.55 + 0.45 * fall))
            m = valid[:, :, None]
            colour[y0:y1, x0:x1] = np.where(m, shaded, colour[y0:y1, x0:x1])
            depth[y0:y1, x0:x1] = np.where(valid, t.astype(np.float32),
                                           depth[y0:y1, x0:x1])

        # Lens vignette + sensor noise: mild, but enough that the clip does not look
        # synthetically perfect to a detector tuned on real footage.
        r2 = ((self.dir_x * FX / (WIDTH / 2)) ** 2 + (self.dir_y * FY / (HEIGHT / 2)) ** 2)
        colour *= (1.0 - 0.16 * r2)[:, :, None]
        colour += rng.normal(0.0, 1.6, colour.shape).astype(np.float32)
        return np.clip(colour, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------------------
# Ground-truth point cloud
# --------------------------------------------------------------------------------------

def sample_cloud(planes: list[Plane], rng: np.random.Generator,
                 total: int = GT_POINTS) -> tuple[np.ndarray, np.ndarray]:
    areas = np.array([p.area for p in planes])
    counts = np.maximum((areas / areas.sum() * total).astype(int), 24)
    xyz_all, rgb_all = [], []
    for p, n in zip(planes, counts, strict=True):
        a = rng.random(n).astype(np.float64)
        b = rng.random(n).astype(np.float64)
        xyz = p.origin[None, :] + a[:, None] * p.u[None, :] + b[:, None] * p.v[None, :]
        th, tw = p.tex.shape[:2]
        ix = np.clip((a * (tw - 1)).astype(int), 0, tw - 1)
        iy = np.clip((b * (th - 1)).astype(int), 0, th - 1)
        bgr = p.tex[iy, ix] * p.shade
        xyz_all.append(xyz)
        rgb_all.append(np.clip(bgr[:, ::-1], 0, 255).astype(np.uint8))
    return np.concatenate(xyz_all), np.concatenate(rgb_all)


def write_ply(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"comment Driftless synthetic ground-truth cloud, {len(xyz)} points, metres\n"
        f"element vertex {len(xyz)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    with path.open("wb") as fh:
        fh.write(header.encode("ascii"))
        for (x, y, z), (r, g, b) in zip(xyz.astype(np.float32), rgb, strict=True):
            fh.write(struct.pack("<fffBBB", x, y, z, int(r), int(g), int(b)))


# --------------------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------------------

SCENES = {
    "loop": {
        "video": "synthetic_loop.mp4",
        "stem": "synthetic_loop",
        "name": "Synthetic room loop",
        "description": ("Virtual pinhole camera on a closed elliptical path inside a "
                        "textured 7 m room. Returns to the start pose, so loop closure "
                        "has a true match. Ships with ground-truth trajectory and cloud."),
        "scene": room_scene,
        "trajectory": loop_trajectory,
        "closed": True,
        "seed": 20260914,
    },
    "corridor": {
        "video": "synthetic_corridor.mp4",
        "stem": "synthetic_corridor",
        "name": "Synthetic corridor (open-ended)",
        "description": ("Straight 9.6 m forward translation down a textured corridor. No "
                        "revisited viewpoint, so loop closure cannot fire and drift "
                        "accumulates monotonically. Ships with ground truth."),
        "scene": corridor_scene,
        "trajectory": corridor_trajectory,
        "closed": False,
        "seed": 4711,
    },
}


def encode(frames_iter, path: Path, fps: float) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{WIDTH}x{HEIGHT}",
        "-r", f"{fps:g}", "-i", "-",
        "-c:v", "libx264", "-preset", "slow", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for frame in frames_iter:
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit(f"ffmpeg failed for {path}")


def build(key: str, seconds: float, out_dir: Path) -> dict[str, object]:
    spec = SCENES[key]
    n = int(round(seconds * FPS))
    rng = np.random.default_rng(int(spec["seed"]))
    print(f"[{key}] building scene…", flush=True)
    planes = spec["scene"](rng)
    centres, Rs = spec["trajectory"](n, np.random.default_rng(int(spec["seed"]) + 1))
    renderer = Renderer(planes)
    orb = cv2.ORB_create(1200)

    counts: list[int] = []
    started = time.time()

    def frames():
        noise_rng = np.random.default_rng(int(spec["seed"]) + 2)
        for i in range(n):
            img = renderer.render(centres[i], Rs[i], noise_rng)
            counts.append(len(orb.detect(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None)))
            if (i + 1) % 30 == 0:
                print(f"  [{key}] {i + 1}/{n} frames, {time.time() - started:5.1f}s, "
                      f"orb median so far {int(np.median(counts))}", flush=True)
            yield img

    video_path = out_dir / str(spec["video"])
    encode(frames(), video_path, FPS)

    traj = []
    tum_lines = []
    length = 0.0
    for i in range(n):
        qw, qx, qy, qz = mat_to_quat(Rs[i])
        pos = centres[i]
        traj.append({"frame_index": i, "t_s": round(i / FPS, 6),
                     "position": [round(float(v), 6) for v in pos],
                     "quaternion": [round(float(q), 8) for q in (qw, qx, qy, qz)]})
        tum_lines.append(f"{i / FPS:.6f} {pos[0]:.6f} {pos[1]:.6f} {pos[2]:.6f} "
                         f"{qx:.8f} {qy:.8f} {qz:.8f} {qw:.8f}")
        if i:
            length += float(np.linalg.norm(centres[i] - centres[i - 1]))
    if spec["closed"]:
        length += float(np.linalg.norm(centres[0] - centres[-1]))

    stem = str(spec["stem"])
    gt = {
        "video": str(spec["video"]),
        "generator": "generate_synthetic.py",
        "units": "metres",
        "width": WIDTH, "height": HEIGHT, "fps": FPS, "frame_count": n,
        "intrinsics": {"model": "pinhole", "fx": FX, "fy": FY, "cx": CX, "cy": CY,
                       "distortion": [0.0, 0.0, 0.0, 0.0, 0.0]},
        "convention": (
            "OpenCV camera: +Z forward, +X right, +Y down. World frame is the fixed scene "
            "frame, which coincides with the frame-0 camera only up to the frame-0 pose "
            "given below. Each entry is T_wc (world-from-camera): `position` is the camera "
            "centre in world coordinates and `quaternion` is [qw, qx, qy, qz] of the "
            "world-from-camera rotation, i.e. p_world = R * p_camera + position."
        ),
        "closed_loop": bool(spec["closed"]),
        "trajectory_length_m": round(length, 4),
        "loop_closure_gap_m": round(float(np.linalg.norm(centres[0] - centres[-1])), 6),
        "frames": traj,
    }
    (out_dir / f"{stem}_gt.json").write_text(json.dumps(gt, indent=2) + "\n", encoding="utf-8")
    (out_dir / f"{stem}_gt_tum.txt").write_text(
        "# timestamp tx ty tz qx qy qz qw\n" + "\n".join(tum_lines) + "\n", encoding="utf-8")

    xyz, rgb = sample_cloud(planes, np.random.default_rng(int(spec["seed"]) + 3))
    write_ply(out_dir / f"{stem}_gt.ply", xyz, rgb)

    median = int(np.median(counts))
    result = {
        "id": stem, "name": spec["name"], "description": spec["description"],
        "duration_s": round(n / FPS, 3),
        # `url` is the public API route per the contract; `file` is the path relative to
        # this directory, which is how the backend locates the media on disk.
        "url": f"/api/v1/samples/{stem}/file", "file": str(spec["video"]),
        "_orb_median": median, "_orb_min": int(np.min(counts)),
        "_orb_p05": int(np.percentile(counts, 5)),
        "_points": len(xyz),
        "_size_mb": round(video_path.stat().st_size / 1e6, 2),
    }
    print(f"[{key}] done in {time.time() - started:.1f}s — {video_path.name} "
          f"{result['_size_mb']} MB, ORB median {median} (min {result['_orb_min']}), "
          f"{len(xyz)} GT points", flush=True)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", choices=sorted(SCENES), action="append")
    ap.add_argument("--seconds", type=float, default=SECONDS)
    ap.add_argument("--out", type=Path, default=HERE)
    ap.add_argument("--no-manifest", action="store_true")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    keys = args.scene or ["loop", "corridor"]
    results = [build(k, args.seconds, args.out) for k in keys]

    if not args.no_manifest and set(keys) == set(SCENES):
        entries = [{k: r[k] for k in ("id", "name", "description", "duration_s", "url", "file")}
                   for r in results]
        path = args.out / "manifest.json"
        # Merge rather than overwrite: prepare_real_clip.py owns its own entry.
        existing = json.loads(path.read_text()) if path.exists() else []
        ours = {e["id"] for e in entries}
        path.write_text(json.dumps(
            entries + [e for e in existing if e.get("id") not in ours],
            indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}")

    print("\nORB (cv2.ORB_create(1200)) per frame")
    for r in results:
        print(f"  {r['id']:<20} median {r['_orb_median']:>5}  "
              f"p05 {r['_orb_p05']:>5}  min {r['_orb_min']:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
