"""Grid-bucketed ORB extraction and descriptor matching.

Plain ``ORB_create(nfeatures=1200).detectAndCompute`` clumps keypoints onto the
few high-contrast patches in a frame, which makes PnP ill-conditioned and biases
triangulation. We over-detect with a relaxed FAST threshold and then retain the
strongest responses per image cell, which is the same idea as ORB-SLAM's
per-cell FAST but runs as two OpenCV calls instead of ``rows*cols`` of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from operator import itemgetter

import cv2
import numpy as np

from .config import SlamConfig

Array = np.ndarray


_EMPTY = (
    np.zeros((0, 2), np.float32),
    np.zeros((0,), np.int32),
    np.zeros((0, 32), np.uint8),
)


@dataclass
class Frame:
    """Everything the tracker needs about one processed video frame."""

    index: int
    t_s: float
    gray: Array
    bgr_small: Array          # kept for RGB sampling of new map points
    kps: Array                # (N, 2) float32 pixel coordinates
    octaves: Array            # (N,) int32 pyramid level
    desc: Array               # (N, 32) uint8 ORB descriptors


class FeatureExtractor:
    def __init__(self, cfg: SlamConfig) -> None:
        self.cfg = cfg
        # Over-detect 2x so bucketing has candidates in every cell. Beyond 2x
        # the FAST count saturates and only detect() cost grows.
        self._orb = self._make(cfg.fast_threshold)
        self._orb_relaxed = self._make(cfg.fast_threshold_min)

    def _make(self, fast_threshold: int) -> cv2.ORB:
        return cv2.ORB_create(
            nfeatures=int(self.cfg.max_features * 2),
            scaleFactor=self.cfg.orb_scale_factor,
            nlevels=self.cfg.orb_levels,
            edgeThreshold=19,
            firstLevel=0,
            WTA_K=2,
            scoreType=cv2.ORB_HARRIS_SCORE,
            patchSize=31,
            fastThreshold=fast_threshold,
        )

    def detect(self, gray: Array) -> tuple[Array, Array, Array]:
        """Return (pts Nx2 float32, octaves N int32, desc Nx32 uint8)."""
        kps = self._orb.detect(gray, None)
        if len(kps) < self.cfg.max_features // 2:
            # Low-texture frame: retry once with the relaxed FAST threshold
            # rather than letting tracking starve.
            kps = self._orb_relaxed.detect(gray, None)
        if not kps:
            return _EMPTY
        kps = self._bucket(kps, gray.shape[1], gray.shape[0])
        kps, desc = self._orb.compute(gray, kps)
        if desc is None or not kps:
            return _EMPTY
        # KeyPoint_convert is a C++ bulk accessor; the equivalent list
        # comprehension over .pt costs 0.19 ms per frame, which is 0.6% of the
        # whole per-frame budget for nothing.
        pts = cv2.KeyPoint_convert(kps).astype(np.float32, copy=False)
        octaves = np.fromiter((k.octave for k in kps), np.int32, len(kps))
        return pts, octaves, np.ascontiguousarray(desc)

    def _bucket(self, kps: list[cv2.KeyPoint], width: int, height: int) -> list[cv2.KeyPoint]:
        cfg = self.cfg
        n = len(kps)
        if n <= cfg.max_features:
            return kps
        pts = cv2.KeyPoint_convert(kps)
        resp = np.fromiter((k.response for k in kps), np.float32, n)
        cw = width / cfg.grid_cols
        ch = height / cfg.grid_rows
        col = np.clip((pts[:, 0] / cw).astype(np.int32), 0, cfg.grid_cols - 1)
        row = np.clip((pts[:, 1] / ch).astype(np.int32), 0, cfg.grid_rows - 1)
        cell = row * cfg.grid_cols + col

        ncells = cfg.grid_cols * cfg.grid_rows
        per_cell = max(1, int(np.ceil(cfg.max_features / ncells)))

        order = np.lexsort((-resp, cell))
        sorted_cell = cell[order]
        # Rank each keypoint inside its cell without a Python loop: the running
        # max of "index where this cell started" gives the per-cell offset.
        idx = np.arange(n)
        is_first = np.empty(n, dtype=bool)
        is_first[0] = True
        np.not_equal(sorted_cell[1:], sorted_cell[:-1], out=is_first[1:])
        start = np.maximum.accumulate(np.where(is_first, idx, 0))
        rank = idx - start

        keep = np.zeros(n, dtype=bool)
        keep[order[rank < per_cell]] = True
        deficit = cfg.max_features - int(keep.sum())
        if deficit > 0:
            rest = np.flatnonzero(~keep)
            if rest.size:
                extra = rest[np.argsort(-resp[rest])[:deficit]]
                keep[extra] = True
        elif deficit < 0:
            kept = np.flatnonzero(keep)
            drop = kept[np.argsort(resp[kept])[: -deficit]]
            keep[drop] = False
        return list(itemgetter(*np.flatnonzero(keep).tolist())(kps))


_INT32_MAX = 2147483647


class Matcher:
    """Hamming matching with a Lowe ratio test, kept array-native.

    ``cv2.batchDistance`` does the same work as ``BFMatcher.knnMatch`` but hands
    back NumPy arrays instead of a list of ``DMatch`` objects, so the ratio test
    stays vectorised (measured ~0.65 ms vs ~1.1 ms for 1200x1200 descriptors).
    """

    def __init__(self, cfg: SlamConfig) -> None:
        self.cfg = cfg

    def ratio_match(
        self,
        desc_a: Array,
        desc_b: Array,
        mask: Array | None = None,
        ratio: float | None = None,
        max_distance: int = 64,
    ) -> tuple[Array, Array, Array]:
        """Return (ia, ib, hamming distance) for the surviving matches."""
        empty = (np.zeros(0, np.int32), np.zeros(0, np.int32), np.zeros(0, np.float32))
        if desc_a.shape[0] == 0 or desc_b.shape[0] < 2:
            return empty
        if mask is not None and not mask.any():
            return empty
        ratio = self.cfg.ratio_test if ratio is None else ratio
        dist, nidx = cv2.batchDistance(
            np.ascontiguousarray(desc_a),
            np.ascontiguousarray(desc_b),
            cv2.CV_32S,
            normType=cv2.NORM_HAMMING,
            K=2,
            mask=mask,
        )
        best = dist[:, 0].astype(np.float32)
        second = dist[:, 1].astype(np.float32)
        idx = nidx[:, 0]
        # A single candidate yields second == INT32_MAX, which passes the ratio
        # test by construction -- that is the behaviour we want under a mask.
        keep = (idx >= 0) & (best <= max_distance) & (best < ratio * second)
        ia = np.flatnonzero(keep).astype(np.int32)
        return ia, idx[keep].astype(np.int32), best[keep]

    def mutual_match(
        self,
        desc_a: Array,
        desc_b: Array,
        ratio: float | None = None,
        mask: Array | None = None,
        max_distance: int = 64,
    ) -> tuple[Array, Array]:
        """Ratio test in both directions; used for init, triangulation and loops.

        Under a geometric mask the both-ways test is what keeps the pair honest:
        an epipolar band is a *line*, and a wrong match anywhere along it
        triangulates to a point that reprojects perfectly in both views, so it
        survives every downstream geometric check and then never matches again.
        """
        ia, ib, _ = self.ratio_match(
            desc_a, desc_b, mask=mask, ratio=ratio, max_distance=max_distance
        )
        if ia.size == 0:
            return ia, ib
        back_mask = None if mask is None else np.ascontiguousarray(mask.T)
        ja, jb, _ = self.ratio_match(
            desc_b, desc_a, mask=back_mask, ratio=ratio, max_distance=max_distance
        )
        back = np.full(desc_b.shape[0], -1, dtype=np.int32)
        back[ja] = jb
        keep = back[ib] == ia
        return ia[keep], ib[keep]


def dedupe_by_distance(ia: Array, ib: Array, dist: Array) -> tuple[Array, Array]:
    """Keep one query per train index, the closest one.

    Guided matching projects many map points into the same neighbourhood, so
    several of them can claim the same keypoint; letting both through would feed
    PnP duplicate observations of one measurement.
    """
    if ia.size == 0:
        return ia, ib
    order = np.lexsort((dist, ib))
    ib_sorted = ib[order]
    first = np.empty(ib_sorted.size, dtype=bool)
    first[0] = True
    np.not_equal(ib_sorted[1:], ib_sorted[:-1], out=first[1:])
    keep = order[first]
    return ia[keep], ib[keep]


def epipolar_mask(F: Array, kps_a: Array, kps_b: Array, threshold: float = 4.0) -> Array:
    """uint8 (M, N) gate: 1 where b lies within `threshold` px of a's epipolar line.

    Unconstrained mutual matching between two keyframes recovers only ~25% of
    the features on repetitive indoor texture, which starves new-point
    triangulation. Gating on the known relative pose instead lets the ratio test
    run much looser for the same outlier rate -- this is ORB-SLAM's
    SearchForTriangulation, and it is one BLAS matmul.
    """
    m, n = kps_a.shape[0], kps_b.shape[0]
    if m == 0 or n == 0:
        return np.zeros((m, n), dtype=np.uint8)
    ha = np.concatenate([kps_a, np.ones((m, 1), np.float32)], axis=1).astype(np.float32)
    hb = np.concatenate([kps_b, np.ones((n, 1), np.float32)], axis=1).astype(np.float32)
    lines = ha @ F.T.astype(np.float32)
    scale = np.sqrt(lines[:, 0] ** 2 + lines[:, 1] ** 2)
    np.maximum(scale, 1e-6, out=scale)
    dist = np.abs(lines @ hb.T)
    return (dist < (threshold * scale)[:, None]).view(np.uint8)


def proximity_mask(proj_uv: Array, kps: Array, radius: float) -> Array:
    """uint8 (M, N) gate: 1 where a keypoint is within ~`radius` px of a projection.

    Handing OpenCV a mask keeps the O(M*N) Hamming comparisons in C++ while the
    geometric gating stays one vectorised NumPy broadcast. Quantising to cells
    first halves the cost versus float differencing (1.2 ms vs 2.1 ms at
    800x1200) at the price of a slightly loose, square search window -- the PnP
    RANSAC downstream removes what the square admits over the disc.
    """
    m, n = proj_uv.shape[0], kps.shape[0]
    if m == 0 or n == 0:
        return np.zeros((m, n), dtype=np.uint8)
    cell = max(radius * 0.75, 1.0)
    pc = (proj_uv * (1.0 / cell)).astype(np.int16)
    kc = (kps * (1.0 / cell)).astype(np.int16)
    near_u = np.abs(pc[:, 0][:, None] - kc[None, :, 0]) <= 1
    near_v = np.abs(pc[:, 1][:, None] - kc[None, :, 1]) <= 1
    return (near_u & near_v).view(np.uint8)
