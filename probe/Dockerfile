# syntax=docker/dockerfile:1.7

# Stage 1: xray-core ----------------------------------------------------
# Multi-arch (linux/amd64, linux/arm64). Always build on the host's
# native platform — the `--platform=$BUILDPLATFORM` flag pins this
# stage regardless of the multi-arch target list, so xray is downloaded
# exactly once with the right architecture.
FROM --platform=$BUILDPLATFORM teddysun/xray:latest AS xray

# Stage 2: Python probe -------------------------------------------------
# Targets the architecture the image will run on. We copy the xray
# binary out of stage 1 so the final image only contains what the probe
# needs.
FROM --platform=$TARGETPLATFORM python:3.12-slim AS probe

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Pull the xray binary that matches the runtime platform. The COPY is
# unconditional — `teddysun/xray` ships both architectures in
# /usr/bin/xray so the kernel/VFS will pick the right one once the
# image is extracted on the target.
COPY --from=xray /usr/bin/xray /usr/local/bin/xray

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

# xray-core dials out via SOCKS on 127.0.0.1:1080 — the probe itself
# does not expose any external listener. We still drop to an unprivileged
# user so a compromised xray cannot escalate inside the container.
RUN useradd --system --uid 1000 --shell /usr/sbin/nologin probe
USER probe:probe

ENTRYPOINT ["python", "-m", "xspeed_probe.main"]
CMD []