"""Tunable knobs for the Driftless SLAM pipeline.

Every default here is a point on the accuracy/latency curve chosen to hit the
"10 s of video in <=10 s of wall clock on 8 vCPU" budget. The comments call out
what each knob costs when you move it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SlamConfig:
    # ---- ingest -----------------------------------------------------------
    target_width: int = 640          # 640 is the sweet spot: ORB cost scales with pixels
    max_frames: int = 1800           # hard cap so a 10 min upload cannot wedge a worker
    frame_stride: int = 1            # >1 trades tracking robustness for throughput

    # ---- calibration ------------------------------------------------------
    # Consumer video ships no intrinsics. We assume a pinhole camera with square
    # pixels and the principal point at the image centre, then refine the focal
    # length from two-view geometry during initialisation.
    fx: float | None = None
    fy: float | None = None
    cx: float | None = None
    cy: float | None = None
    default_focal_ratio: float = 0.8   # fx = ratio * width; ~64 deg HFOV, mid-range for
    #                                    phones (~0.85) and webcams (~0.7)
    refine_focal: bool = True
    # Two-view self-calibration only ever gets the focal to ~15%; once a few
    # keyframes of real 3D structure exist, letting bundle adjustment own fx=fy
    # for one run is far better conditioned. Skipped when the caller supplies a
    # calibration.
    refine_focal_ba: bool = True
    focal_ba_keyframe: int = 6
    focal_search_lo: float = 0.55
    focal_search_hi: float = 1.45
    focal_search_steps: int = 13
    focal_prior_weight: float = 0.02  # weak MAP prior: it only decides the answer when
    #                                   the two-view data term is flat, which is exactly the
    #                                   rotation-dominated / pure-forward degenerate case

    # ---- features ---------------------------------------------------------
    max_features: int = 1200
    orb_scale_factor: float = 1.2
    orb_levels: int = 8
    fast_threshold: int = 12
    fast_threshold_min: int = 5      # relaxed retry when a frame is texture-poor
    grid_cols: int = 8
    grid_rows: int = 6
    ratio_test: float = 0.8    # 0.75 is the textbook value but throws away ~45% of
    #                            correct matches on repetitive indoor texture; RANSAC
    #                            downstream is cheaper than starving the tracker

    # ---- initialisation ---------------------------------------------------
    init_min_matches: int = 80
    init_min_inliers: int = 60
    init_min_parallax_deg: float = 2.0
    init_min_points: int = 100
    init_max_reference_age: int = 45  # slide the reference forward if init keeps failing

    # ---- tracking ---------------------------------------------------------
    track_search_radius_px: float = 14.0
    track_radius_max_scale: float = 6.0  # cap on the motion-adaptive search window
    track_match_ratio: float = 0.95      # the proximity gate already does the filtering
    track_match_ratio_tight: float = 0.80  # ...but a wide window needs a strict test
    track_match_max_distance: int = 90   # ORB TH_HIGH; appearance drifts as the view rotates
    track_min_inliers: int = 18
    track_pnp_reproj_px: float = 3.0
    track_local_kf_window: int = 12
    relocalise_min_inliers: int = 16
    relocalise_after_frames: int = 3   # dead-reckon first; BoW relocalisation is a last resort

    # ---- keyframes / local mapping ----------------------------------------
    # Floor on keyframe spacing, and the main brake on keyframe density, which is what
    # drives optimize_ms (local BA runs once per keyframe). Raising it to 4 is tempting:
    # it cuts optimisation ~35% and on samples/synthetic_loop.mp4 it restores the loop
    # closure and halves ATE (0.72 -> 0.39 m). It is rejected because it also drops that
    # clip's pose coverage from 298/300 to 229/300 -- a quarter of the trajectory has no
    # pose at all. Buying the latency target by silently discarding frames is not a
    # trade this project makes; see tests/test_pipeline.py, which asserts >= 250.
    kf_min_frame_gap: int = 3
    kf_max_frame_gap: int = 18
    kf_tracked_ratio: float = 0.80
    # tan(3 deg): insert once there is enough baseline to triangulate well, whatever
    # the tracker's inlier count happens to be doing
    # Insert once there is enough baseline to triangulate well, whatever the
    # tracker's inlier count happens to be doing. Lowering this to 0.052 (3 deg)
    # takes samples/desk_handheld_tum.mp4 from 157/300 tracked frames to 287/300,
    # but roughly doubles the keyframe count and costs ~45% more wall time, which
    # does not fit the 10 s budget on the deployment host. See docs in the README.
    kf_min_parallax_ratio: float = 0.13
    kf_min_rotation_deg: float = 8.0     # pans rotate the map out of view
    kf_min_tracked: int = 45
    triangulate_min_parallax_deg: float = 2.5
    triangulate_neighbours: int = 12     # pairs actually triangulated, best baseline first
    triangulate_neighbour_pool: int = 20  # covisible keyframes considered for that choice
    triangulate_recent_pool: int = 12     # plus the last N by index, covisible or not
    triangulate_target_new: int = 150     # stop consulting shorter baselines past this
    # A new point much farther than the map it is being added to is almost always a
    # low-parallax artefact, and those are what drive monocular scale drift: an
    # over-long depth inflates the next pose, which inflates the next triangulation.
    # Measured on samples/synthetic_loop.mp4 this cap plus baseline-ordered neighbour
    # selection took end-of-loop scale inflation from 7.4x to ~1.1x.
    new_point_depth_ratio: float = 3.0
    triangulate_match_ratio: float = 0.85
    triangulate_match_max_distance: int = 60
    map_point_max_reproj_px: float = 4.0
    # Culling at 3 observations is ORB-SLAM's rule, but it assumes ORB-SLAM's match
    # rates. Here it removed ~60% of the map and tracking died around frame 220;
    # at 2 the map is 2.5x denser, coverage is 298/300 and accuracy is unchanged.
    map_point_min_obs: int = 2

    # ---- local bundle adjustment ------------------------------------------
    enable_local_ba: bool = True
    ba_window: int = 7               # free keyframes; covisible neighbours are fixed anchors
    # The single biggest lever on optimize_ms, and local BA is 40-47% of wall time
    # on the deployment host. Dropping 20 -> 12 cuts optimisation 26-30% and, measured
    # against ground truth on all three sample clips, *improves* reprojection error
    # (1.417->1.271, 0.532->0.505, 1.174->0.739 px) and ATE on two of three. The trust
    # region reaches a good step well before iteration 20; the extra iterations were
    # refining below the noise floor of the correspondences.
    #
    # Be careful retuning this. The pipeline is deterministic per config but genuinely
    # sensitive to this value: keyframe insertion and loop detection are threshold
    # comparisons, so a small shift in pose estimates flips discrete decisions. nfev=14
    # yields 53 keyframes on the handheld clip where 12 and 16 yield ~31. Sweeping for
    # a local optimum here is fitting the sample clips, not tuning the algorithm.
    ba_max_nfev: int = 12
    ba_huber_px: float = 2.0
    ba_min_observations: int = 40
    ba_every_n_keyframes: int = 1

    # ---- loop closure -----------------------------------------------------
    enable_loop_closure: bool = True
    lc_min_kf_gap: int = 20          # temporal gap; stops "closing" against the previous frame
    lc_score_ratio: float = 0.72     # candidate must score >= ratio * best covisible score
    lc_min_score: float = 0.018
    lc_max_candidates: int = 4
    lc_consistency_hits: int = 2     # candidate must recur in covisibility-consistent groups
    lc_min_matches: int = 24
    lc_min_sim3_inliers: int = 20
    lc_min_inlier_ratio: float = 0.35   # guards against a confident wrong closure
    lc_cooldown_kf: int = 15            # one closure already re-anchors the whole graph
    lc_ransac_iters: int = 400
    lc_match_ratio: float = 0.9
    lc_match_max_distance: int = 80
    lc_min_guided_matches: int = 45   # points re-found by projecting through the Sim(3)
    lc_guided_radius_px: float = 12.0
    lc_candidate_window: int = 8      # covisible keyframes pooled around a candidate
    lc_inlier_ratio_thresh: float = 0.10  # Sim(3) inlier radius as a fraction of the
    #                                       *destination* keyframe's median depth
    lc_max_correction_ratio: float = 1.2  # loop error vs total trajectory length
    lc_min_pgo_reduction: float = 0.75    # a true closure's residual collapses
    pgo_iterations: int = 12
    pgo_scale_information: float = 1.0   # identity information lets accumulated scale
    #                                      error redistribute around the loop instead of
    #                                      collapsing onto the closure edge (ORB-SLAM's choice)
    # Pose-graph optimisation restores the *trajectory* but leaves map points
    # attached to their anchor keyframe, so a closure always costs some
    # map-to-pose consistency. One global BA pass afterwards buys it back.
    global_ba_after_closure: bool = True
    global_ba_max_nfev: int = 60
    enable_global_ba: bool = False   # unconditional final polish; off for latency

    # ---- output -----------------------------------------------------------
    max_points_web: int = 60000

    # ---- runtime ----------------------------------------------------------
    progress_interval_s: float = 0.15
    queue_size: int = 8
    threaded: bool = True
    seed: int = 0

    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("extra", None)
        return d
