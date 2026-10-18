# syntax=docker/dockerfile:1.7

# Stage 1: xray-core ----------------------------------------------------
# Multi-arch (linux/amd64, linux/arm64). Always build on the host's
# native platform — the `--platform=$BUILDPLATFORM` flag pins this
# stage regardless of the multi-arch target list, so xray is downloaded
# exactly once with the right architecture.
#
# Pinned to a known release tag instead of :latest (DAT-459, item o)
# so unexpected upstream changes / CVE patches cannot break production
# builds without an explicit bump.
#
# 26.10.17 (Oct 2026) is the minimum that supports the ``xhttp``
# transport used by the vless URIs handed out by the df3 upstream
# (DAT-461). Older 1.8.x builds reject it with
# ``unknown transport protocol: xhttp`` — the probe then writes a
# ``xray_start_failed`` result and the operator cannot tell whether
# the failure came from the link or the egress.
ARG XRAY_VERSION=26.10.17
FROM --platform=$BUILDPLATFORM teddysun/xray:${XRAY_VERSION} AS xray

# Stage 2: Python probe -------------------------------------------------
# Targets the architecture the image will run on. We copy the xray
# binary out of stage 1 so the final image only contains what the probe
# needs.
FROM --platform=$TARGETPLATFORM python:3.12-slim AS probe

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    XSPEED_PROBE_XRAY_VERSION=${XRAY_VERSION}

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