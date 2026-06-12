# syntax=docker/dockerfile:1
#
# Synthesis runner image for the queue-triggered Azure Container Apps Job
# (ADR 0001 Option C, #67 follow-up 2/5, #77). It bakes in ffmpeg/ffprobe —
# the native deps podcaster/audio.py shells out to for concat, two-pass
# loudnorm, and ffprobe validation — and runs the existing
# podcaster.job_runner against the synthesis Storage Queue.
#
# Identity-only at runtime: the runner uses managed identity (IMDS) for Blob,
# Queue, and Azure OpenAI TTS, so no account keys or connection strings are
# baked into the image.
#
# Base image pinned by digest (python:3.11-slim, multi-arch index) so rebuilds
# are reproducible and the supply chain is auditable.
FROM python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0

# ffmpeg pulls in ffprobe; both are required by podcaster/audio.py. Apply
# available security upgrades to the pinned base so OS packages (e.g. openssl)
# pick up fixes published after the base image was built.
RUN apt-get update \
    && apt-get -y upgrade \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Non-root runtime user.
RUN groupadd --system synth \
    && useradd --system --gid synth --home-dir /app --shell /usr/sbin/nologin synth

# Install Python deps first for better layer caching. The runner itself uses
# only the standard library, but requirements.txt is honored so the image
# matches the repo's pinned dependency set.
COPY requirements.txt ./
# Install deps, then remove the build toolchain (pip/setuptools/wheel). The
# runner imports only the standard library, so dropping the toolchain keeps the
# image free of build-tooling CVEs without affecting runtime behaviour.
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip uninstall -y pip setuptools wheel

# Application code (synthesis pipeline is reused unchanged from podcaster/).
COPY podcaster ./podcaster

# Bundle music assets for intro/outro mixing.
COPY assets ./assets

USER synth

# Consume the synthesis queue and drive the existing episode.py pipeline.
ENTRYPOINT ["python", "-m", "podcaster.job_runner"]
