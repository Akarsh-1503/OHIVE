"""LeadForge VLM: Qwen2.5-VL served on Modal behind an OpenAI-compatible `/v1` API.

The backend talks to this with the stock `openai` python client:

    OpenAI(base_url="https://<workspace>--ak-project-vlm-serve.modal.run/v1",
           api_key=os.environ["VLM_API_KEY"])

Deploy with `modal deploy qwen_vlm.py`. See README.md for the full runbook.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request

import modal

# --- What we serve -----------------------------------------------------------
# The revision is pinned so an upstream re-upload can never silently change the
# model behind a deployed endpoint. To swap models, change both constants
# together (e.g. "Qwen/Qwen2.5-VL-7B-Instruct-AWQ" + its commit sha) and re-run
# `modal run qwen_vlm.py::download_model` before `modal deploy`.
MODEL_NAME = "Qwen/Qwen2.5-VL-3B-Instruct"
MODEL_REVISION = "66285546d2b821cf421d4f5eb2576359d3770cd3"

VLLM_VERSION = "0.28.0"
GPU_TYPE = "L4"
PORT = 8000

# A business card arrives downscaled to 1280 px on the longest edge. Qwen2.5-VL
# emits one visual token per 28x28 patch, so capping pixels caps prompt length:
# 1_003_520 / 784 = 1280 visual tokens, leaving ~2.7k of the window for the
# JSON-schema grammar and the generated lead record.
MAX_MODEL_LEN = 4096
MAX_PIXELS = 1_003_520
MIN_PIXELS = 200_704

# A real trade, not a free win. Across 8 cold starts on L4 with warm caches,
# eager booted in a median 210 s against 246 s for torch.compile + CUDA graphs,
# and gave up roughly 20% of decode throughput. Eager wins here only because
# this endpoint scales to zero: on a cold 25-card batch the faster boot more
# than pays for the slower decode. Set False if the endpoint is kept warm.
ENFORCE_EAGER = True

# One container should absorb a whole 25-card batch rather than fanning out to
# fresh GPUs that each pay a cold start. target_inputs matches the backend's
# VLM_CONCURRENCY; max_inputs leaves headroom for the health poller and retries.
MAX_CONCURRENT_INPUTS = 10
TARGET_CONCURRENT_INPUTS = 6

HF_CACHE_DIR = "/root/.cache/huggingface"
VLLM_CACHE_DIR = "/root/.cache/vllm"

hf_cache = modal.Volume.from_name("ak-project-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("ak-project-vllm-cache", create_if_missing=True)
auth_secret = modal.Secret.from_name("ak-project-vlm-auth", required_keys=["VLM_API_KEY"])

vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install(f"vllm=={VLLM_VERSION}", "huggingface_hub[hf_xet]")
    .env(
        {
            "HF_XET_HIGH_PERFORMANCE": "1",
            "HF_HOME": HF_CACHE_DIR,
            # Inductor and Triton otherwise write to /tmp and $HOME, which are
            # ephemeral on Modal, so their artefacts are thrown away every cold
            # start. Redirecting them into the cache Volume did not measurably
            # move cold start (vLLM's warmup is dominated by dummy forward
            # passes, not compilation) but it does keep the caches reusable if
            # ENFORCE_EAGER is turned off.
            "VLLM_CACHE_ROOT": VLLM_CACHE_DIR,
            "TORCHINDUCTOR_CACHE_DIR": f"{VLLM_CACHE_DIR}/torchinductor",
            "TRITON_CACHE_DIR": f"{VLLM_CACHE_DIR}/triton",
        }
    )
    .add_local_python_source("vlm_auth")
)

app = modal.App("ak-project-vlm")


@app.function(
    image=vllm_image,
    volumes={HF_CACHE_DIR: hf_cache},
    cpu=4.0,
    memory=16384,
    timeout=60 * 60,
)
def download_model() -> None:
    """Populate the weights Volume from a CPU container, so no GPU time is burnt on I/O."""
    from huggingface_hub import snapshot_download

    started = time.monotonic()
    path = snapshot_download(
        MODEL_NAME,
        revision=MODEL_REVISION,
        ignore_patterns=["*.pt", "*.bin", "*.pth"],
    )
    hf_cache.commit()
    print(f"cached {MODEL_NAME}@{MODEL_REVISION[:8]} -> {path} in {time.monotonic() - started:.1f}s")


def _serve_command(api_key: str) -> list[str]:
    mm_processor_kwargs = json.dumps({"min_pixels": MIN_PIXELS, "max_pixels": MAX_PIXELS})
    cmd = [
        "vllm",
        "serve",
        MODEL_NAME,
        "--revision",
        MODEL_REVISION,
        "--served-model-name",
        MODEL_NAME,
        "--host",
        "0.0.0.0",
        "--port",
        str(PORT),
        "--api-key",
        api_key,
        "--max-model-len",
        str(MAX_MODEL_LEN),
        "--max-num-seqs",
        str(MAX_CONCURRENT_INPUTS),
        "--gpu-memory-utilization",
        "0.90",
        # video=0 is load-bearing, not decoration: left unset, vLLM profiles and
        # warms the engine against a maximum-size *video*, which cost ~2 min of
        # every cold start for a capability this endpoint never exposes.
        "--limit-mm-per-prompt",
        json.dumps({"image": 1, "video": 0}),
        "--mm-processor-kwargs",
        mm_processor_kwargs,
        "--structured-outputs-config.backend",
        "xgrammar",
        # vLLM's own key check only covers /v1; see vlm_auth for what this closes.
        "--middleware",
        "vlm_auth.RequireBearer",
        "--uvicorn-log-level",
        "warning",
    ]
    if ENFORCE_EAGER:
        cmd.append("--enforce-eager")
    return cmd


def _wait_until_ready(proc: subprocess.Popen[bytes], api_key: str, timeout_s: float) -> float:
    """Block until vLLM answers /health, or fail fast if the engine died."""
    url = f"http://127.0.0.1:{PORT}/health"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"vLLM exited during startup with code {proc.returncode}")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status == 200:
                    return time.monotonic() - started
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    raise TimeoutError(f"vLLM did not become healthy within {timeout_s:.0f}s")


@app.function(
    image=vllm_image,
    gpu=GPU_TYPE,
    volumes={HF_CACHE_DIR: hf_cache, VLLM_CACHE_DIR: vllm_cache},
    secrets=[auth_secret],
    min_containers=0,
    max_containers=2,
    scaledown_window=900,
    timeout=60 * 60,
)
@modal.concurrent(max_inputs=MAX_CONCURRENT_INPUTS, target_inputs=TARGET_CONCURRENT_INPUTS)
@modal.web_server(port=PORT, startup_timeout=15 * 60)
def serve() -> None:
    api_key = os.environ["VLM_API_KEY"]
    # Modal puts added local sources on /root, but vLLM runs as a child process, so
    # the path has to be handed down explicitly for `--middleware` to import.
    env = {**os.environ, "PYTHONPATH": f"/root:{os.environ.get('PYTHONPATH', '')}"}
    proc = subprocess.Popen(_serve_command(api_key), env=env)
    ready_in = _wait_until_ready(proc, api_key, timeout_s=13 * 60)
    # Persist whatever JIT artefacts this boot produced so the next cold start
    # reuses them instead of recompiling.
    vllm_cache.commit()
    print(f"vLLM healthy after {ready_in:.1f}s (enforce_eager={ENFORCE_EAGER})")
