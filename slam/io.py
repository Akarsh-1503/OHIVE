"""Export formats: binary PLY point clouds and TUM trajectories."""

from __future__ import annotations

from pathlib import Path

import numpy as np

Array = np.ndarray

_PLY_HEADER = """ply
format binary_little_endian 1.0
comment Driftless monocular SLAM, reconstruction is up to scale
element vertex {n}
property float x
property float y
property float z
property uchar red
property uchar green
property uchar blue
end_header
"""

_PLY_DTYPE = np.dtype(
    [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("red", "u1"), ("green", "u1"), ("blue", "u1")]
)


def write_ply(path: str | Path, xyz: Array, rgb: Array) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(xyz.shape[0])
    rec = np.empty(n, dtype=_PLY_DTYPE)
    rec["x"] = xyz[:, 0]
    rec["y"] = xyz[:, 1]
    rec["z"] = xyz[:, 2]
    rec["red"] = rgb[:, 0]
    rec["green"] = rgb[:, 1]
    rec["blue"] = rgb[:, 2]
    with path.open("wb") as fh:
        fh.write(_PLY_HEADER.format(n=n).encode("ascii"))
        fh.write(rec.tobytes())
    return path


def write_tum(path: str | Path, times: Array, positions: Array, quats_wxyz: Array) -> Path:
    """TUM format: `timestamp tx ty tz qx qy qz qw` (note the scalar-last order)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = np.column_stack(
        [
            times,
            positions,
            quats_wxyz[:, 1],
            quats_wxyz[:, 2],
            quats_wxyz[:, 3],
            quats_wxyz[:, 0],
        ]
    )
    with path.open("w", encoding="ascii") as fh:
        fh.write("# timestamp tx ty tz qx qy qz qw\n")
        np.savetxt(fh, rows, fmt="%.9g")
    return path
