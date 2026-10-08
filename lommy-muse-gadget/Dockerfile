# Muse Gadget (Linux Device SDK) as an Umbrel app.
#
# Mirrors the official installer (linux/install.sh):
#   - Debian userland with the BlueZ/D-Bus Python bindings
#   - venv with --system-site-packages, SDK pip-installed into it
#   - daemon runs as an unprivileged account, never root
#
# Upstream: https://github.com/facebookincubator/muse-gadget-sdk (Apache-2.0)
# Pinned to a specific upstream commit for reproducible builds; bump SDK_COMMIT
# to pick up upstream changes.

FROM debian:bookworm-slim

ARG SDK_COMMIT=b139b45064b4dcecf7bfe97e75bc7f99c10c28b6

ENV DEBIAN_FRONTEND=noninteractive \
    MUSEGADGET_STATE_DIR=/data/state \
    STATUS_PORT=8756

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-dbus \
        python3-gi \
        bluez \
        curl \
        ca-certificates \
        git \
        docker.io \
    && rm -rf /var/lib/apt/lists/*

# Install the SDK the way install.sh does: venv with system site packages
# (so dbus/gi come from apt), then pip install the linux/ subtree.
RUN python3 -m venv --system-site-packages /opt/musegadget/venv \
    && /opt/musegadget/venv/bin/pip install --no-cache-dir \
        "git+https://github.com/facebookincubator/muse-gadget-sdk@${SDK_COMMIT}#subdirectory=linux"

# Unprivileged account that Muse's commands run as (the --run-as equivalent).
RUN useradd -m -s /bin/bash gadget

COPY entrypoint.sh /entrypoint.sh
COPY status.py /opt/musegadget/status.py
COPY bt_fix.py /opt/musegadget/bt_fix.py
COPY integrations.py /opt/musegadget/integrations.py
RUN chmod +x /entrypoint.sh

VOLUME ["/data/state"]
EXPOSE 8756

ENTRYPOINT ["/entrypoint.sh"]
