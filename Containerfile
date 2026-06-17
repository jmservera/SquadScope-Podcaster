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
FROM python:3.11-slim@sha256:ae52c5bef62a6bdd42cd1e8dffef86b9cd284bde9427da79839de7a4b983e7ca

# ffmpeg pulls in ffprobe; both are required by podcaster/audio.py. Apply
# available security upgrades to the pinned base so OS packages (e.g. openssl)
# pick up fixes published after the base image was built.
RUN apt-get update \
    && apt-get -y upgrade \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

WORKDIR /app

# Non-root runtime user.
RUN groupadd --system synth \
    && useradd --system --gid synth --home-dir /app --shell /usr/sbin/nologin synth

# Install Python deps first for better layer caching.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser for the video pipeline (intro/outro
# HTML rendering). --with-deps pulls required system libraries (libnss3,
# libatk, etc.) so a separate apt-get layer is unnecessary.  Clean up the
# apt lists left behind by the Playwright installer to keep the layer lean.
RUN python -m playwright install chromium --with-deps \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Drop build toolchain — not needed at runtime.
RUN python -m pip uninstall -y pip setuptools wheel

# Application code (synthesis pipeline is reused unchanged from podcaster/).
COPY podcaster ./podcaster

# Bundle music assets for intro/outro mixing.
COPY assets ./assets

USER synth

# Consume the synthesis queue and drive the existing episode.py pipeline.
ENTRYPOINT ["python", "-m", "podcaster.job_runner"]
