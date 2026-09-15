"""Driftless — monocular RGB sparse point-cloud SLAM.

    from slam import SlamPipeline, SlamConfig

    recon = SlamPipeline(SlamConfig()).run("clip.mp4")
    recon.to_dict(); recon.to_ply("cloud.ply"); recon.to_tum("traj.txt")

The reconstruction is metric **up to an unknown global scale**: a single moving
camera cannot observe absolute distance. The initial baseline is fixed to 1.0
and every length in the output is in those units.
"""

import os as _os

from .cpu import numeric_threads as _numeric_threads

# Must run before numpy/OpenCV are imported below: OpenBLAS and OMP read these once at
# load time and ignore later changes. `setdefault` so an explicit caller setting wins.
for _var in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    _os.environ.setdefault(_var, str(_numeric_threads()))

# Deliberately below the thread-pool setup above, not at the top of the file: importing
# these pulls in numpy and OpenCV, which latch their pool sizes at load time.
from .config import SlamConfig  # noqa: E402
from .pipeline import SlamPipeline  # noqa: E402
from .results import DriftMetrics, Metrics, PoseRecord, Reconstruction  # noqa: E402

__all__ = [
    "SlamConfig",
    "SlamPipeline",
    "Reconstruction",
    "Metrics",
    "DriftMetrics",
    "PoseRecord",
]

__version__ = "1.0.0"
