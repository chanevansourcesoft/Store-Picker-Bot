# syntax=docker/dockerfile:1
# Policy environment for the Jetson AGX Orin (JetPack 6.2 / L4T r36.4).
# The base image provides Python 3.12 and a CUDA build of torch/torchvision for the Orin GPU;
# every other package comes from uv.lock so versions match dev machines and Nebius.
FROM dustynv/pytorch:2.7-r36.4.0-cu128-24.04@sha256:4bb7f0a3852f86629ed271aaf6c4aa000305cc4e4673b8e9f334668ee1b22bc8

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

# libsndfile1: needed by soundfile, which transformers imports
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/policy
COPY pyproject.toml uv.lock ./

# Install locked packages into the image's /opt/venv, skipping the GPU stack (torch, torchvision,
# CUDA libraries) that the base image already provides. torchcodec has no build for this torch,
# so LeRobot falls back to PyAV for video.
RUN --mount=type=cache,target=/root/.cache/uv \
    /usr/local/bin/uv export --frozen --no-hashes --no-emit-project --no-dev \
    | grep -vE '^(torch|torchvision|torchcodec|triton)==|^(nvidia-|cuda-)' \
    > /tmp/requirements.txt \
    && /usr/local/bin/uv pip install --python /opt/venv/bin/python --index-url https://pypi.org/simple \
       --no-deps -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
