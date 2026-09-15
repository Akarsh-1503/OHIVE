"""CPU accounting, deliberately free of numpy/OpenCV imports.

This lives apart from `pipeline` because `slam/__init__` has to size the numeric
backends' thread pools *before* those libraries are imported -- OpenBLAS and OMP read
their environment once at load time. Importing anything heavy from here would load
them first and silently defeat the pinning.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


def available_cpus() -> int:
    """CPUs this process may actually use, not the ones the host happens to own.

    `os.cpu_count()` reports the machine. Inside a container it is wrong in the
    direction that hurts: the cgroup grants a fraction of the host, and every numeric
    library that sizes its thread pool from `cpu_count()` then oversubscribes. Measured
    on a 24-core host with an 8-CPU quota, that cost 40% -- 17.5 s against 12.2 s on the
    same clip -- purely in scheduler contention.
    """
    quota = 0
    # cgroup v2, then v1. "max" means unlimited, in which case fall through.
    with contextlib.suppress(OSError, ValueError, IndexError):
        raw = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if raw[0] != "max":
            quota = -(-int(raw[0]) // int(raw[1]))  # ceil, so 6.5 CPUs -> 7
    if not quota:
        with contextlib.suppress(OSError, ValueError):
            us = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
            period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
            if us > 0 and period > 0:
                quota = -(-us // period)
    # sched_getaffinity respects CPU pinning, which a cgroup quota does not express.
    affinity = 0
    with contextlib.suppress(AttributeError, OSError):
        affinity = len(os.sched_getaffinity(0))
    candidates = [n for n in (quota, affinity, os.cpu_count() or 0) if n > 0]
    return min(candidates) if candidates else 1


def numeric_threads() -> int:
    """Thread-pool size for OpenCV and the BLAS backends.

    Capped at 4 on purpose. The pipeline is a single-threaded main loop with two helper
    threads (decode, ORB); its hot calls -- `solvePnPRansac`, `batchDistance`, the BA
    linear solve -- are efficient single-threaded C++. A larger pool buys contention
    rather than throughput, and on this workload more CPUs measured *slower*.
    """
    return max(1, min(4, available_cpus()))
