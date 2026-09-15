"""Binary PLY and TUM exports, parsed back byte for byte."""

from __future__ import annotations

import numpy as np

from slam.geometry import quat_from_R, so3_exp
from slam.io import write_ply, write_tum

_PLY_DTYPE = np.dtype(
    [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("red", "u1"), ("green", "u1"), ("blue", "u1")]
)


def _read_ply(path) -> tuple[dict[str, str], np.ndarray]:
    raw = path.read_bytes()
    end = raw.index(b"end_header\n") + len(b"end_header\n")
    header = raw[:end].decode("ascii")
    return header, np.frombuffer(raw[end:], dtype=_PLY_DTYPE)


class TestPly:
    def test_roundtrip(self, tmp_path, rng: np.random.Generator) -> None:
        xyz = rng.normal(size=(500, 3)) * 3.0
        rgb = rng.integers(0, 256, (500, 3)).astype(np.uint8)
        header, rec = _read_ply(write_ply(tmp_path / "c.ply", xyz, rgb))

        assert "format binary_little_endian 1.0" in header
        assert "element vertex 500" in header
        assert "up to scale" in header          # the caveat must travel with the file
        assert rec.shape == (500,)
        assert np.allclose(
            np.stack([rec["x"], rec["y"], rec["z"]], axis=1), xyz.astype(np.float32)
        )
        assert np.array_equal(
            np.stack([rec["red"], rec["green"], rec["blue"]], axis=1), rgb
        )

    def test_empty_cloud(self, tmp_path) -> None:
        header, rec = _read_ply(
            write_ply(tmp_path / "e.ply", np.zeros((0, 3)), np.zeros((0, 3), np.uint8))
        )
        assert "element vertex 0" in header
        assert rec.shape == (0,)

    def test_creates_parent_directories(self, tmp_path) -> None:
        out = write_ply(
            tmp_path / "a" / "b" / "c.ply", np.zeros((1, 3)), np.zeros((1, 3), np.uint8)
        )
        assert out.exists()


class TestTum:
    def test_is_scalar_last(self, tmp_path, rng: np.random.Generator) -> None:
        """TUM is `tx ty tz qx qy qz qw`; our own convention is scalar-first."""
        n = 20
        times = np.arange(n) / 30.0
        pos = rng.normal(size=(n, 3))
        quats = np.array([quat_from_R(so3_exp(rng.normal(size=3) * 0.4)) for _ in range(n)])

        path = write_tum(tmp_path / "t.txt", times, pos, quats)
        lines = path.read_text().splitlines()
        assert lines[0].startswith("#")
        rows = np.array([[float(v) for v in ln.split()] for ln in lines[1:]])

        assert rows.shape == (n, 8)
        assert np.allclose(rows[:, 0], times)
        assert np.allclose(rows[:, 1:4], pos, atol=1e-6)
        assert np.allclose(rows[:, 4:7], quats[:, 1:4], atol=1e-6)   # qx qy qz
        assert np.allclose(rows[:, 7], quats[:, 0], atol=1e-6)       # qw last
        assert np.allclose(np.linalg.norm(rows[:, 4:8], axis=1), 1.0, atol=1e-6)

    def test_empty_trajectory(self, tmp_path) -> None:
        path = write_tum(
            tmp_path / "t.txt", np.zeros(0), np.zeros((0, 3)), np.zeros((0, 4))
        )
        assert [ln for ln in path.read_text().splitlines() if not ln.startswith("#")] == []
