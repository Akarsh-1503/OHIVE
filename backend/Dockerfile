# Driftless API (`slam-api`).
#
# BUILD CONTEXT IS `assignment2/`, not `assignment2/backend/`, because the image has to
# install the sibling SLAM engine from the repo:
#
#   docker build -f backend/Dockerfile -t slam-api assignment2/
#
# The engine is installed if `slam/` carries a pyproject/setup; if it is not there yet the
# build still succeeds and the image runs with SLAM_BACKEND=stub.

FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv

# Dependency layer first so source edits do not invalidate the wheel cache.
COPY backend/pyproject.toml /build/backend/pyproject.toml
RUN pip install \
      "fastapi~=0.115.0" \
      "uvicorn[standard]~=0.32.0" \
      "pydantic~=2.9.0" \
      "python-multipart~=0.0.12" \
      "numpy~=2.1.0" \
      "opencv-python-headless~=4.10.0"

COPY backend/app /build/backend/app
COPY slam /build/slam
RUN if [ -f /build/slam/pyproject.toml ] || [ -f /build/slam/setup.py ]; then \
        pip install /build/slam; \
    else \
        echo "WARNING: assignment2/slam has no installable package; image is stub-only."; \
    fi


FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/data \
    SAMPLES_DIR=/samples \
    PORT=8000

# libglib2.0-0 is the only shared object the headless OpenCV wheels still dlopen.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 driftless \
 && useradd --system --uid 10001 --gid driftless --home /app driftless

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=driftless:driftless backend/app /app/app

RUN mkdir -p /data /samples && chown -R driftless:driftless /data /samples

USER driftless
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"

# The lifespan hook swaps uvicorn's loggers onto the JSON handler once the app starts.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
