"""Shared fixtures. Puts the repo root on sys.path so `import slam` works in-tree."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

SAMPLES = ROOT / "samples"


def load_sample(stem: str) -> tuple[Path, np.ndarray, dict]:
    """(video path, (N,3) ground-truth camera centres, ground-truth metadata)."""
    video = SAMPLES / f"{stem}.mp4"
    meta_path = SAMPLES / f"{stem}_gt.json"
    if not video.exists() or not meta_path.exists():
        pytest.skip(f"sample clip {stem} not present")
    meta = json.loads(meta_path.read_text())
    pos = np.array([f["position"] for f in meta["frames"]], dtype=np.float64)
    return video, pos, meta


@pytest.fixture(scope="session")
def loop_sample() -> tuple[Path, np.ndarray, dict]:
    return load_sample("synthetic_loop")


@pytest.fixture(scope="session")
def corridor_sample() -> tuple[Path, np.ndarray, dict]:
    return load_sample("synthetic_corridor")


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(12345)
