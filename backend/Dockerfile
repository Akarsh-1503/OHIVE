# syntax=docker/dockerfile:1

# ---- build ------------------------------------------------------------------
# Dependencies are resolved into a self-contained venv so the runtime image needs
# no pip, no build toolchain and no package metadata.
FROM python:3.11-slim-bookworm AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip setuptools wheel \
 && pip install .

# ---- runtime ----------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/data \
    VLM_PROVIDER=stub \
    VLM_MODEL=Qwen/Qwen2.5-VL-3B-Instruct

# libexpat1 is pulled in by pypdfium2's PDF renderer at import time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 leadforge \
 && useradd --system --uid 10001 --gid leadforge --home /srv/leadforge leadforge

COPY --from=build /opt/venv /opt/venv
WORKDIR /srv/leadforge
COPY --chown=leadforge:leadforge app ./app

RUN mkdir -p /data && chown -R leadforge:leadforge /data /srv/leadforge
VOLUME ["/data"]
USER leadforge

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
