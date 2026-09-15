"""Grid-bucketed detection, masked matching and the geometric gates."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from slam.config import SlamConfig
from slam.features import (
    FeatureExtractor,
    Matcher,
    dedupe_by_distance,
    epipolar_mask,
    proximity_mask,
)


@pytest.fixture(scope="module")
def textured_image() -> np.ndarray:
    """Deliberately lopsided texture: rich on the left, sparse on the right."""
    rng = np.random.default_rng(7)
    img = np.full((360, 640), 90, dtype=np.uint8)
    for _ in range(900):
        x, y = int(rng.uniform(0, 300)), int(rng.uniform(0, 360))
        cv2.circle(img, (x, y), int(rng.integers(2, 7)), int(rng.integers(0, 255)), -1)
    for _ in range(120):
        x, y = int(rng.uniform(300, 640)), int(rng.uniform(0, 360))
        cv2.circle(img, (x, y), int(rng.integers(3, 9)), int(rng.integers(0, 255)), -1)
    return cv2.GaussianBlur(img, (0, 0), 1.2)


class TestExtractor:
    def test_respects_the_feature_cap(self, textured_image: np.ndarray) -> None:
        cfg = SlamConfig(max_features=600)
        pts, octaves, desc = FeatureExtractor(cfg).detect(textured_image)
        assert pts.shape[0] <= cfg.max_features
        assert pts.shape[0] == octaves.shape[0] == desc.shape[0]
        assert desc.shape[1] == 32
        assert desc.dtype == np.uint8

    def test_bucketing_spreads_keypoints(self, textured_image: np.ndarray) -> None:
        """The point of bucketing: cells must not be winner-take-all."""
        cfg = SlamConfig(max_features=800, grid_cols=8, grid_rows=6)
        pts, _, _ = FeatureExtractor(cfg).detect(textured_image)
        col = np.clip((pts[:, 0] / (640 / 8)).astype(int), 0, 7)
        row = np.clip((pts[:, 1] / (360 / 6)).astype(int), 0, 5)
        counts = np.bincount(row * 8 + col, minlength=48)

        plain = cv2.ORB_create(nfeatures=cfg.max_features)
        raw = cv2.KeyPoint_convert(plain.detect(textured_image, None))
        rcol = np.clip((raw[:, 0] / (640 / 8)).astype(int), 0, 7)
        rrow = np.clip((raw[:, 1] / (360 / 6)).astype(int), 0, 5)
        raw_counts = np.bincount(rrow * 8 + rcol, minlength=48)

        assert (counts > 0).sum() >= (raw_counts > 0).sum()
        assert counts.max() / max(counts.mean(), 1e-9) < raw_counts.max() / max(
            raw_counts.mean(), 1e-9
        )

    def test_empty_image_returns_empty_arrays(self) -> None:
        pts, octaves, desc = FeatureExtractor(SlamConfig()).detect(
            np.zeros((120, 160), np.uint8)
        )
        assert pts.shape == (0, 2) and octaves.shape == (0,) and desc.shape == (0, 32)


class TestMatcher:
    def test_identical_descriptors_match_themselves(self) -> None:
        rng = np.random.default_rng(3)
        desc = rng.integers(0, 256, (200, 32), dtype=np.uint8)
        ia, ib = Matcher(SlamConfig()).mutual_match(desc, desc)
        assert ia.size > 150
        assert np.array_equal(ia, ib)

    def test_mask_restricts_candidates(self) -> None:
        rng = np.random.default_rng(4)
        a = rng.integers(0, 256, (50, 32), dtype=np.uint8)
        b = np.concatenate([a, rng.integers(0, 256, (50, 32), dtype=np.uint8)])
        mask = np.zeros((50, 100), np.uint8)
        mask[:, 50:] = 1                     # forbid the true correspondences
        ia, ib, _ = Matcher(SlamConfig()).ratio_match(a, b, mask=mask)
        assert (ib >= 50).all()

    def test_empty_inputs(self) -> None:
        m = Matcher(SlamConfig())
        ia, ib, d = m.ratio_match(np.zeros((0, 32), np.uint8), np.zeros((5, 32), np.uint8))
        assert ia.size == ib.size == d.size == 0


class TestGates:
    def test_proximity_mask_keeps_near_pairs(self) -> None:
        proj = np.array([[100.0, 100.0], [400.0, 200.0]], np.float32)
        kps = np.array([[102.0, 99.0], [401.0, 203.0], [10.0, 10.0]], np.float32)
        mask = proximity_mask(proj, kps, 14.0)
        assert mask.shape == (2, 3) and mask.dtype == np.uint8
        assert mask[0, 0] == 1 and mask[1, 1] == 1
        assert mask[0, 2] == 0 and mask[1, 2] == 0

    def test_epipolar_mask_follows_the_line(self) -> None:
        """A horizontal-baseline rig puts corresponding points on the same row."""
        K = np.array([[500.0, 0.0, 320.0], [0.0, 500.0, 180.0], [0.0, 0.0, 1.0]])
        t = np.array([0.5, 0.0, 0.0])
        E = np.array([[0, -t[2], t[1]], [t[2], 0, -t[0]], [-t[1], t[0], 0]]) @ np.eye(3)
        Kinv = np.linalg.inv(K)
        F = Kinv.T @ E @ Kinv
        a = np.array([[320.0, 180.0]], np.float32)
        b = np.array([[200.0, 180.0], [200.0, 260.0]], np.float32)
        mask = epipolar_mask(F, a, b, threshold=4.0)
        assert mask[0, 0] == 1
        assert mask[0, 1] == 0

    def test_dedupe_keeps_the_closest_claim(self) -> None:
        ia = np.array([0, 1, 2], np.int32)
        ib = np.array([5, 5, 7], np.int32)
        dist = np.array([30.0, 10.0, 20.0], np.float32)
        ka, kb = dedupe_by_distance(ia, ib, dist)
        assert sorted(zip(ka.tolist(), kb.tolist())) == [(1, 5), (2, 7)]
