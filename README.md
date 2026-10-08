# Muse Gadget for Umbrel

An Umbrel Community App Store containing **Muse Gadget**: Meta's open-source
[Linux Device SDK](https://github.com/facebookincubator/muse-gadget-sdk)
(Apache-2.0) packaged as an Umbrel app. Pair it once with the Muse app on your
phone, and Muse can run shell commands, read/write files, and report device
health on your Umbrel from chat — e.g. managing your other Umbrel apps through
Docker, or reaching other LAN devices such as Home Assistant over HTTP.

> **Raspberry Pi only.** The prebuilt image is arm64, so this app runs on
> umbrelOS on a Pi (3B+, 4, 5, Zero 2 W) — not on x86 Umbrel installs.
> Pairing also needs the Pi's onboard Bluetooth LE.

## Features

- **Pair in your browser** — paste your SDK token and tap Start pairing; no SSH needed after install.
- **Chat control** — ask Muse in chat to run shell commands, read/write files, or check device health on your Umbrel.
- **Docker access** — manage sibling Umbrel containers through the mounted Docker socket.
  A toggle in the web UI lets you disable this entirely if you'd rather Muse couldn't touch your other apps.
- **App integrations** — give Muse API access to 12 popular self-hosted apps
  (Home Assistant, Immich, Jellyfin, Nextcloud, Paperless-ngx, Audiobookshelf,
  Mealie, n8n, Syncthing, Sonarr, Radarr, Jellyseerr) or any custom HTTP API,
  each with its own on/off toggle. Keys are stored only on the device and are
  unreadable while disabled (except via Docker access — see Security notes).
- **Organized web UI** — Home (status, pairing), Permissions (the Docker
  toggle with an honest Can/Can't/Privacy breakdown), and Integrations pages
  behind a burger menu; "Exit App" returns to the Umbrel dashboard.
- **LAN reach** — the gadget shares your network, so it can call local HTTP APIs like Home Assistant.
- **Unprivileged by default** — the daemon runs as a non-root `gadget` user; your token is stored with mode 600 on the device only.

## How it works

- The container runs the `musegadget` daemon as an unprivileged `gadget` user
  (the container equivalent of `install.sh --run-as gadget`).
- Your SDK token is passed as an env var (`MUSEGADGET_SDK_TOKEN`) — it is
  never baked into the image or committed to git.
- Device identity + pairing state persist in an Umbrel-managed volume.
- The host Docker socket is mounted so the gadget can manage sibling
  containers (same privilege tradeoff as giving the host install Docker
  access, declared in the compose file instead). The entrypoint detects
  the host's docker group GID from the socket itself, and a toggle in the
  web UI can revoke the gadget's Docker access entirely.
- The host D-Bus socket is mounted so Bluetooth LE pairing with the Muse app
  works from inside the container, and so the one-tap Bluetooth fix can
  restart the host's bluetooth service after you confirm it.
- A setup wizard on port 8756 backs the Umbrel dashboard tile: first run
  asks for the SDK token (stored only on the device) and offers a
  one-tap pairing button — no SSH needed after install.
- The entrypoint supervises the daemon: it starts once a token exists,
  restarts it on failure, and picks up token changes.

## Setup

The image is prebuilt for arm64 by GitHub Actions on every push — there is
nothing to build. After installing, open the app's web UI: it walks you
through the SDK token and pairing, including the Bluetooth fix below.

### 1. Host Bluetooth prep

You can skip the manual steps below — the app's web UI handles the
Bluetooth fix for you: if the host's BlueZ is missing the Android-pairing
tweak (`ExchangeMTU = 256` under `[GATT]`), the page shows an unmissable
banner and a **Review & apply the fix** button. It shows exactly what will
change, backs up the original config, applies the one-line edit, and
restarts the host's bluetooth service — with a one-tap restore if you ever
want the original back. No SSH needed.

(The host's Docker group GID is detected automatically from the Docker
socket — there is no manual step for it.)

<details>
<summary>Doing the MTU tweak by hand (optional fallback)</summary>

The tweak caps the negotiated GATT MTU at 256: the Muse Android app writes
(MTU - 3)-byte packets and fails when BlueZ negotiates above 512 — the same
cap the ESP32 firmware uses. On the Umbrel host (via SSH):

```bash
ssh umbrel@umbrel.local
# BlueZ must be running for Bluetooth LE pairing:
sudo systemctl enable --now bluetooth

# Keep the negotiated GATT MTU at 256 (the Muse Android app needs this):
sudo python3 - <<'EOF'
import re
p = "/etc/bluetooth/main.conf"
t = open(p).read() if __import__("os").path.exists(p) else ""
if not re.search(r"^\s*ExchangeMTU\s*=\s*256\s*$", t, re.M):
    t = re.sub(r"^\s*#\s*ExchangeMTU\s*=.*$", "ExchangeMTU = 256", t, count=1, flags=re.M) \
        if re.search(r"^\s*#\s*ExchangeMTU\s*=.*$", t, re.M) else \
        t.rstrip("\n") + "\n\n[GATT]\nExchangeMTU = 256\n"
    open(p, "w").write(t)
    print("set ExchangeMTU = 256")
else:
    print("already set")
EOF

sudo systemctl restart bluetooth
```

</details>

**iPhone only:** BlueZ's battery plugin tries to read the phone's battery
level, which iPhones answer only over a bonded link — so BlueZ asks the
phone to bond, the gadget refuses, and iOS drops the pairing. The app's
Bluetooth page has a one-tap **iPhone fix** for this: it writes a systemd
drop-in that restarts bluetoothd with `--noplugin=battery` (your current
bluetoothd path is detected automatically), with a one-tap restore.

<details>
<summary>Doing it by hand (optional fallback)</summary>

```bash
sudo mkdir -p /etc/systemd/system/bluetooth.service.d
sudo tee /etc/systemd/system/bluetooth.service.d/zz-musegadget.conf >/dev/null <<'EOF'
# Written for the musegadget container: never ask phones to bond.
[Service]
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd --noplugin=battery
EOF
sudo systemctl daemon-reload && sudo systemctl restart bluetooth
```

(If your umbrelOS keeps `bluetoothd` at a different path, adjust the
`ExecStart=` line to match `systemctl cat bluetooth.service`. The
`--noplugin=battery` part is what matters.)
</details>

### 2. Add this repo as a community store

In Umbrel: **App Store → Community App Stores** → add:

```text
https://github.com/thefuckisalommy/muse-on-umbrel
```

### 3. Install from the Umbrel dashboard

Install **Muse Gadget** from your new community store. Umbrel pulls the
prebuilt image and starts the app. Check progress with
`docker logs -f lommy-muse-gadget_musegadget_1`
(use `docker ps` to confirm the exact container name).

### 4. Enter your token and pair — in the app's web UI, no SSH

Open the app from the Umbrel dashboard (or browse to
`http://umbrel.local:8756`):

1. Paste your SDK token from `gadgets.muse.ai` (Account → SDK tokens) and
   save. The gadget connects within seconds.
2. Tap **Start pairing**, then on your phone: Muse app → **Settings →
   Devices → Developer mode ON → Add Device** → choose the
   `MuseGadgetXXXXXX` shown on the page. Pairing stays open 10 minutes.

The page also warns you if the host Bluetooth MTU tweak from step 1 was
skipped (Android pairing fails without it).

### Advanced (all optional)

- **Pre-seed the token**: create
  `~/umbrel/app-data/lommy-muse-gadget/token.env` containing
  `MUSE_GADGET_TOKEN=mgst_...` (mode 600) before installing; the setup form
  is skipped. (The host's Docker group GID is detected automatically —
  no need to set `DOCKER_GID`.)
- **Pair from the terminal**:
  `docker exec -it -u gadget $(docker ps -q -f name=musegadget) /opt/musegadget/venv/bin/musegadget pair`
  (add `--force` to pair again).

## Updating the SDK

The image pins an upstream commit (`SDK_COMMIT` in the Dockerfile). To pick
up SDK changes: bump the SHA and push — GitHub Actions rebuilds the image
automatically. Then bump `version` in `umbrel-app.yml` so Umbrel offers the
update to installed users.

## Security notes

- The `gadget` user is unprivileged on the host; its power comes from the
  Docker socket mount, which is effectively host-root-equivalent. This is
  inherent to managing sibling containers — same as Portainer.
- The SDK token is root-only on a normal install; here it lives in
  `token.env` (mode 600, advanced pre-seed) or in `/data/state/sdk_token`
  (mode 600, written by the setup wizard) — both on the Umbrel host only,
  never in the image or git. The setup page is LAN-only and the form hides
  once a token is saved.
- Pair on a trusted network: community devices have no manufacturer
  verification.
- App-integration keys are stored in `/data/state/keys/` (mode 600)
  and never leave the device. Turning an integration off chmods its key
  to 000, so nothing on the device — including Muse's own commands — can
  read it until re-enabled. Exception: full Docker access (above) can read
  those key files regardless, since it is root-equivalent — the Docker
  toggle is the master switch.

## App integrations

The web UI's **App integrations** card gives Muse API-level access to
popular Umbrel apps, each with an individual toggle:

| App | Credential | Where to create it |
|-----|-----------|-------------------|
| Home Assistant | Long-lived access token | Profile → Long-lived access tokens |
| Immich | API key | Account settings → API keys |
| Jellyfin | API key | Dashboard → API keys |
| Nextcloud | App password (+ username) | Settings → Security |
| Paperless-ngx | API token | Profile → API tokens |
| Audiobookshelf | API token | Settings → Users → your user → API tokens |
| Mealie | API token | Profile → Manage API tokens |
| n8n | API key | Settings → n8n API |
| Syncthing | API key | Actions → Settings → GUI |
| Sonarr / Radarr | API key | Settings → General |
| Jellyseerr | API key | Settings → General |

Need something else? The **Add a custom integration** form at the bottom of
the card covers any HTTP API: name it, give the base URL, pick the auth
style (Bearer, custom header, `Token`, Basic, query parameter, or none),
and set a test path. It gets the same toggle, test and remove controls.

Enter the app's LAN base URL and paste the key; **Test connection** verifies
it before you rely on it. Ask Muse in chat (e.g. "list my Immich albums",
"turn off the living room lights") and it calls the app's API from the Pi.
`INTEGRATIONS.md` (in the app folder) documents the endpoints for contributors.

## Troubleshooting

- **Setup page keeps showing the token form after saving**: the token was
  rejected (it must start with `mgst_`) — copy it fresh from gadgets.muse.ai.
- **Pairing window won't open**: the daemon needs a valid token first; check
  the daemon line on the setup page.
- **Pairing can't reach Bluetooth**: confirm `systemctl is-active bluetooth`
  on the host and that `/run/dbus/system_bus_socket` exists.
- **Docker commands fail inside the gadget**: check the Docker toggle on the
  Permissions page — when it's off, the Docker socket isn't available at all.
  (The host's Docker group GID is detected automatically; no manual setup.)
- **iOS pairing drops**: the `--noplugin=battery` drop-in from step 1
  wasn't applied or `bluetoothd` wasn't restarted.
