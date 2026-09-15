/** Mirrors `_contracts/assignment2-api.md` (v1). Field names are snake_case on purpose. */

export type JobStatus = 'queued' | 'decoding' | 'tracking' | 'optimizing' | 'completed' | 'failed';

export type Stage = 'decoding' | 'tracking' | 'optimizing';

export interface VideoInfo {
  width: number;
  height: number;
  fps: number;
  frame_count: number;
  duration_s: number;
}

export interface Drift {
  pre_optimization_loop_error_m: number;
  post_optimization_loop_error_m: number;
  reduction_pct: number;
  scale_drift_ratio: number;
}

export interface HostInfo {
  cpu: string;
  vcpu: number;
  ram_gb: number;
}

export interface Metrics {
  wall_ms: number;
  queue_wait_ms: number;
  decode_ms: number;
  tracking_ms: number;
  optimize_ms: number;
  frames_processed: number;
  processing_fps: number;
  realtime_factor: number;
  keyframes: number;
  map_points: number;
  mean_reprojection_error_px: number;
  median_track_length: number;
  loop_closures: number;
  loop_candidates_checked: number;
  drift: Drift;
  trajectory_length_m: number;
  ba_runs: number;
  host: HostInfo;
}

export interface Job {
  job_id: string;
  status: JobStatus;
  filename: string;
  created_at: string;
  video: VideoInfo | null;
  metrics: Metrics | null;
  error: string | null;
  queue_position: number | null;
  truncated: boolean;
}

export interface Pose {
  frame_index: number;
  t_s: number;
  is_keyframe: boolean;
  position: [number, number, number];
  quaternion: [number, number, number, number]; // qw, qx, qy, qz — world-from-camera
  tracked_points: number;
  reprojection_error_px: number;
}

export interface LoopClosure {
  from_kf: number;
  to_kf: number;
  inliers: number;
  scale: number;
}

export interface Reconstruction {
  job_id: string;
  poses: Pose[];
  points: {
    xyz: number[];
    rgb: number[];
    observations: number[];
  };
  loop_closures: LoopClosure[];
  metrics: Metrics;
}

export interface Sample {
  id: string;
  name: string;
  description: string;
  duration_s: number;
  url: string;
}

export interface Health {
  status: string;
  version: string;
  workers: number;
  cpu_count: number;
}

export interface JobOptions {
  target_width?: number;
  max_frames?: number;
  enable_loop_closure?: boolean;
}

/* ---- SSE event payloads ---- */

export interface StageEvent {
  stage: Stage;
  message: string;
}

export interface ProgressEvent {
  frames_done: number;
  frames_total: number;
  fps: number;
  keyframes: number;
  map_points: number;
  loop_closures: number;
  elapsed_ms: number;
}

export interface LoopClosureEvent {
  from_kf: number;
  to_kf: number;
  inliers: number;
}

export interface CompletedEvent {
  job_id: string;
  metrics: Metrics;
}

export interface FailedEvent {
  error: string;
}

/* ---- Errors ---- */

export type ApiErrorCode =
  | 'UNSUPPORTED_MEDIA_TYPE'
  | 'FILE_TOO_LARGE'
  | 'VIDEO_TOO_LONG'
  | 'UNDECODABLE_VIDEO'
  | 'EMPTY_UPLOAD'
  | 'INVALID_PARAMETER'
  | 'JOB_NOT_FOUND'
  | 'JOB_NOT_COMPLETED'
  | 'SAMPLE_NOT_FOUND'
  | 'FRAME_NOT_FOUND'
  | 'ARTIFACT_NOT_FOUND'
  | 'INTERNAL_ERROR';
