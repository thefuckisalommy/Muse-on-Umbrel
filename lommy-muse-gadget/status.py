#!/usr/bin/env python3
"""Status page + first-run setup wizard for the Muse Gadget Umbrel app.

Served on port 8756 for the Umbrel dashboard tile. Runs as the unprivileged
'gadget' user.

- No SDK token yet: shows a setup form. Submitting stores the token in
  /data/state/sdk_token (mode 600); the entrypoint supervisor notices within
  seconds and starts the musegadget daemon with it.
- Token present: shows daemon/pairing state, a "Start pairing" button that
  runs `musegadget pair` headless (10-minute window, like the CLI), and a
  Bluetooth MTU advisory read from the host's /etc/bluetooth, which is
  mounted read-only at /host-bluetooth.

The page is LAN-only (Umbrel does not expose app ports to the internet).
Once a token is saved the form is hidden.
"""

import html
import json
import os
import re
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import integrations as integ

STATE_DIR = Path(os.environ.get("MUSEGADGET_STATE_DIR", "/data/state"))
PORT = int(os.environ.get("STATUS_PORT", "8756"))
MUSEGADGET_BIN = "/opt/musegadget/venv/bin/musegadget"
TOKEN_FILE = STATE_DIR / "sdk_token"
PAIR_UNTIL_FILE = STATE_DIR / "pair_until"
PAIR_PID_FILE = STATE_DIR / "pair.pid"
PAIR_LOG_FILE = STATE_DIR / "pair.log"
HOST_BT_CONF = Path("/host-bluetooth/main.conf")
BT_FIX_REQUEST = STATE_DIR / "bt_fix.request"
BT_FIX_RESULT = STATE_DIR / "bt_fix.result"
BT_BACKUP = STATE_DIR / "main.conf.bak"
BT_IPHONE_REQUEST = STATE_DIR / "bt_iphone.request"
BT_IPHONE_RESULT = STATE_DIR / "bt_iphone.result"
BT_IPHONE_DROPIN = Path("/host-systemd/bluetooth.service.d/zz-musegadget.conf")
DOCKER_ENABLED_FILE = STATE_DIR / "docker_enabled"
DOCKER_ACCESS_REQUEST = STATE_DIR / "docker_access.request"
DOCKER_ACCESS_RESULT = STATE_DIR / "docker_access.result"
PAIR_WINDOW_S = 600
MAX_POST_BYTES = 4096

TOKEN_RE = re.compile(r"^mgst_[A-Za-z0-9_\-]{8,}$")


def token_source():
    """'env' (token.env pre-seed), 'file' (setup wizard), or None."""
    if os.environ.get("MUSEGADGET_SDK_TOKEN"):
        return "env"
    try:
        if TOKEN_FILE.stat().st_size > 0:
            return "file"
    except OSError:
        pass
    return None


def effective_token():
    t = os.environ.get("MUSEGADGET_SDK_TOKEN", "").strip()
    if t:
        return t
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def daemon_running():
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if "musegadget" in cmd and " run" in cmd:
            return True
    return False


def pair_window_remaining():
    try:
        until = int(PAIR_UNTIL_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0
    return max(0, until - int(time.time()))


def pair_process_running():
    """Is the background `musegadget pair` process actually alive?"""
    try:
        pid = int(PAIR_PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except OSError:
        return False
    return "musegadget" in cmd and "pair" in cmd


def pair_log_tail(n=25):
    try:
        lines = PAIR_LOG_FILE.read_text(errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:]


def identity_info():
    """Derive the BLE name and node ID from the stored MAC.

    identity.json only stores {"mac": ...}; the display names are derived
    the same way the SDK does: the node id and BLE name end in the same
    six hex digits.
    """
    try:
        mac = json.loads((STATE_DIR / "identity.json").read_text()).get("mac", "")
    except Exception:
        mac = ""
    if not re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", mac or ""):
        return None
    suffix = mac.replace(":", "")[-6:]
    return {"ble_name": "MuseGadget" + suffix.upper(), "node_id": "homelink-" + suffix}


def mtu_status():
    """None = host bluetooth config not visible, True = MTU capped at 256,
    False = not capped (Android pairing will fail)."""
    try:
        text = HOST_BT_CONF.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(";"):
            continue
        m = re.match(r"(?i)ExchangeMTU\s*=\s*(\d+)", s)
        if m:
            return int(m.group(1)) <= 256
    return False


def bt_fix_state():
    """Snapshot of the one-tap Bluetooth fix: whether the MTU cap is
    applied, whether a backup exists (restore possible), whether a
    request is pending, and the latest result."""
    try:
        pending = BT_FIX_REQUEST.exists()
    except OSError:
        pending = False
    try:
        has_backup = BT_BACKUP.exists()
    except OSError:
        has_backup = False
    result = None
    try:
        result = json.loads(BT_FIX_RESULT.read_text())
    except Exception:
        result = None
    return {"pending": pending, "has_backup": has_backup, "result": result}


def request_bt_fix(action):
    """Drop a request file for the root entrypoint to pick up. The web UI
    runs as the unprivileged gadget user and cannot touch the host's
    /etc/bluetooth itself."""
    if action not in ("apply", "restore"):
        return
    try:
        if BT_FIX_REQUEST.exists():
            return  # one already pending; don't stack them
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        BT_FIX_REQUEST.write_text(action + "\n")
        try:
            BT_FIX_RESULT.unlink()  # clear the previous outcome
        except OSError:
            pass
    except OSError:
        pass


def bt_iphone_state():
    """Snapshot of the one-tap iPhone pairing fix: whether the systemd
    drop-in is present, whether a request is pending, and the latest
    result."""
    try:
        applied = BT_IPHONE_DROPIN.exists()
    except OSError:
        applied = False
    try:
        pending = BT_IPHONE_REQUEST.exists()
    except OSError:
        pending = False
    result = None
    try:
        result = json.loads(BT_IPHONE_RESULT.read_text())
    except Exception:
        result = None
    return {"applied": applied, "pending": pending, "result": result}


def request_bt_iphone(action):
    """Drop an iPhone-fix request file for the root entrypoint to pick up.
    Same privilege story as request_bt_fix: the web UI cannot write the
    host's /etc/systemd itself."""
    if action not in ("apply", "restore"):
        return
    try:
        if BT_IPHONE_REQUEST.exists():
            return  # one already pending; don't stack them
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        BT_IPHONE_REQUEST.write_text(action + "\n")
        try:
            BT_IPHONE_RESULT.unlink()  # clear the previous outcome
        except OSError:
            pass
    except OSError:
        pass


def docker_access_state():
    """User toggle for whether the gadget may use the Docker socket."""
    try:
        enabled = DOCKER_ENABLED_FILE.read_text().strip() != "0"
    except OSError:
        enabled = True  # default on: container management is a headline feature
    try:
        pending = DOCKER_ACCESS_REQUEST.exists()
    except OSError:
        pending = False
    result = None
    try:
        result = json.loads(DOCKER_ACCESS_RESULT.read_text())
    except Exception:
        result = None
    return {"enabled": enabled, "pending": pending, "result": result}


def request_docker_toggle():
    """Flip the Docker-access toggle; the root entrypoint applies it and
    restarts the gadget's processes so the group change takes effect."""
    cur = docker_access_state()
    if cur["pending"]:
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        DOCKER_ACCESS_REQUEST.write_text("0\n" if cur["enabled"] else "1\n")
        try:
            DOCKER_ACCESS_RESULT.unlink()
        except OSError:
            pass
    except OSError:
        pass


def start_pairing(token):
    """Open a 10-minute BLE pairing window, headless. Mirrors `musegadget pair`.

    The subprocess PID and its output are recorded so the page can tell a
    live window apart from one that died on launch.
    """
    if pair_window_remaining() > 0 and pair_process_running():
        return  # a window is already open; don't stack them
    args = [MUSEGADGET_BIN, "pair"]
    try:
        if (STATE_DIR / "pairing.json").exists():
            args.append("--force")
    except OSError:
        pass
    env = dict(os.environ, MUSEGADGET_SDK_TOKEN=token)
    try:
        logf = open(PAIR_LOG_FILE, "a")
        have_log = True
    except OSError:
        logf = subprocess.DEVNULL
        have_log = False
    try:
        proc = subprocess.Popen(
            args,
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except OSError:
        return
    finally:
        if have_log:
            logf.close()  # the child keeps its own copy
    try:
        PAIR_PID_FILE.write_text(str(proc.pid))
        PAIR_UNTIL_FILE.write_text(str(int(time.time()) + PAIR_WINDOW_S))
    except OSError:
        pass


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Muse Gadget</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 38rem; margin: 2rem auto;
       padding: 0 1rem; color: #eee; background: #111; line-height: 1.5; }}
.ok {{ color: #4ade80; }} .no {{ color: #f87171; }} .warn {{ color: #fbbf24; }}
code {{ background: #222; padding: 0.15rem 0.4rem; border-radius: 4px;
        word-break: break-all; }}
code.bigname {{ font-size: 1.35rem; font-weight: bold; padding: 0.4rem 0.8rem;
        display: inline-block; margin: 0.3rem 0; }}
.card {{ background: #1a1a1a; border: 1px solid #333; border-radius: 8px;
         padding: 1rem 1.2rem; margin: 1rem 0; }}
input[type=password] {{ width: 100%; box-sizing: border-box; padding: 0.6rem;
         font-size: 1rem; border-radius: 6px; border: 1px solid #444;
         background: #222; color: #eee; margin: 0.5rem 0; }}
input[type=text] {{ width: 100%; box-sizing: border-box; padding: 0.6rem;
         font-size: 1rem; border-radius: 6px; border: 1px solid #444;
         background: #222; color: #eee; margin: 0.5rem 0; }}
select {{ width: 100%; box-sizing: border-box; padding: 0.6rem;
         font-size: 1rem; border-radius: 6px; border: 1px solid #444;
         background: #222; color: #eee; margin: 0.5rem 0; }}
.app {{ border-top: 1px solid #333; padding: 0.8rem 0; }}
.app:first-of-type {{ border-top: 0; }}
details.app summary {{ cursor: pointer; font-size: 1.1rem; font-weight: bold;
         padding: 0.2rem 0; }}
details.app form {{ margin: 0.3rem 0; }}
button {{ padding: 0.6rem 1.2rem; font-size: 1rem; border-radius: 6px; border: 0;
          background: #4f46e5; color: #fff; cursor: pointer; }}
button:hover {{ background: #4338ca; }}
button.danger {{ background: #7f1d1d; }} button.danger:hover {{ background: #991b1b; }}
pre {{ background: #222; padding: 0.8rem; border-radius: 6px; overflow-x: auto; }}
.small {{ color: #999; font-size: 0.9rem; }}
.alert {{ border: 2px solid #f59e0b; background: #2a1f0d; }}
.alert h2 {{ color: #fbbf24; margin-top: 0; }}
.alert button {{ background: #b45309; font-weight: bold; }}
.appbar {{ position: sticky; top: 0; z-index: 50; display: flex; align-items: center;
          justify-content: space-between; background: #111; padding: 0.7rem 0;
          border-bottom: 1px solid #333; }}
.appbar-title {{ font-size: 1.25rem; font-weight: bold; }}
.appbar-actions {{ display: flex; align-items: center; gap: 0.4rem; }}
.iconbtn {{ color: #eee; text-decoration: none; font-size: 1.1rem;
            padding: 0.35rem 0.55rem; border-radius: 6px; line-height: 1; }}
.iconbtn:hover {{ background: #222; }}
.burger {{ position: relative; }}
.burger summary {{ list-style: none; cursor: pointer; font-size: 1.35rem; color: #eee;
                   padding: 0.35rem 0.55rem; border-radius: 6px; line-height: 1; }}
.burger summary::-webkit-details-marker {{ display: none; }}
.burger summary:hover {{ background: #222; }}
.burger nav {{ position: absolute; right: 0; top: calc(100% + 0.35rem); min-width: 12rem;
               background: #1c1c1c; border: 1px solid #444; border-radius: 8px;
               box-shadow: 0 10px 28px rgba(0,0,0,.55); overflow: hidden; z-index: 60; }}
.burger nav a {{ display: block; padding: 0.75rem 1.1rem; color: #eee;
                 text-decoration: none; font-size: 1rem; }}
.burger nav a:hover {{ background: #2b2b2b; }}
.burger nav a.active {{ color: #4ade80; font-weight: bold; }}
</style>{head_extra}</head><body>
<header class="appbar">
<span class="appbar-title">Muse Gadget</span>
<span class="appbar-actions">
<details class="burger">
<summary title="Menu">&#9776;</summary>
<nav>
<a href="/" class="{home_active}">Home</a>
<a href="/bluetooth" class="{bluetooth_active}">Bluetooth</a>
<a href="/?page=permissions" class="{permissions_active}">Permissions</a>
<a href="/?page=integrations" class="{integrations_active}">Integrations</a>
<a href="{umbrel_url}">Exit App</a>
</nav>
</details>
</span>
</header>
{body}
</body></html>"""

SETUP_FORM = """
<div class="card">
<h2>First, add your SDK token</h2>
<p>Get one at <b>gadgets.muse.ai</b> (Account &rarr; SDK tokens), then paste it here.
The gadget connects within seconds — no terminal needed.</p>
{error}
<form method="post" action="/setup">
<input type="password" name="token" placeholder="mgst_…" autocomplete="off"
       autocapitalize="off" spellcheck="false">
<button type="submit">Save token</button>
</form>
<p class="small">Stored only on this device, never shown again.</p>
</div>
"""

STATUS_CARD = """
<div class="card">
<p>SDK token: <b class="ok">{token_how}</b></p>
<p>Daemon: <b class="{dcls}">{daemon}</b></p>
<p>Paired: <b class="{pcls}">{paired}</b></p>
<p>BLE name: <code>{ble}</code></p>
<p>Node ID: <code>{node}</code></p>
{bt_line}
</div>
"""

PAIR_CARD = """
<div class="card">
<h2>Pair with the Muse app</h2>
{pair_section}
</div>
"""

DOCKER_CARD = """
<div class="card">
<h2>What Muse can do on this device</h2>
{docker_html}
</div>
"""

INTEGRATIONS_CARD = """
<div class="card">
<h2>App integrations</h2>
<p class="small">Give Muse API access to your other Umbrel apps. Keys are stored
only on this device and never shown again. Turning an app off makes its key
unreadable, so Muse cannot use it until you turn it back on.</p>
{integrations_html}
</div>
"""

RESET_CARD = """
<div class="card">
<form method="post" action="/reset-token"
      onsubmit="return confirm('Remove the saved token? The gadget will disconnect until a new one is entered.')">
<input type="hidden" name="page" value="{page}">
<button type="submit" class="danger">Remove saved token</button>
</form>
</div>
"""

BT_BANNER = """
<div class="card alert">
<h2>&#9888; Pairing will not work yet</h2>
<p>Your Umbrel host's Bluetooth needs a one-line tweak
(<code>ExchangeMTU = 256</code>) before the Muse app can pair over
Bluetooth LE. <b>Without it, pairing fails.</b></p>
<form method="get" action="/bluetooth"><button type="submit">Review &amp; apply the fix</button></form>
</div>
"""

BT_CONFIRM = """
<div class="card">
<h2>Apply the Bluetooth fix</h2>
<p>This will change <b>one setting</b> on the Umbrel host:</p>
<ol>
<li>Back up <code>/etc/bluetooth/main.conf</code> (kept on the device, restorable below).</li>
<li>Set <code>ExchangeMTU = 256</code> under <code>[GATT]</code> — a minimal edit; comments and everything else are left alone.</li>
<li>Restart the host's <code>bluetooth</code> service so it takes effect (Bluetooth drops for a few seconds).</li>
</ol>
<p class="small">This only lowers the Bluetooth LE packet-size ceiling from 517
to 256 bytes. It does not affect classic Bluetooth audio, Wi-Fi, or any other
app's settings, and it is fully reversible.</p>
{current}
<form method="post" action="/bluetooth/apply">
<button type="submit">Apply the fix</button>
<a href="/" style="margin-left:1rem;color:#999;">Cancel</a>
</form>
<details><summary>Prefer to do it manually?</summary>
<pre>ssh umbrel@umbrel.local
# add under [GATT] in /etc/bluetooth/main.conf:
#   ExchangeMTU = 256
sudo systemctl restart bluetooth</pre>
</details>
</div>
<div class="card">
<h2>Pairing from an iPhone?</h2>
<p>BlueZ's battery plugin tries to read your iPhone's battery level, which
iPhones only answer over a bonded link. The gadget refuses to bond, so iOS
drops the pairing. The fix restarts the host's bluetooth service without the
battery plugin.</p>
<p>This writes <b>one file</b> on the Umbrel host:</p>
<ol>
<li>Create
<code>/etc/systemd/system/bluetooth.service.d/zz-musegadget.conf</code> —
a drop-in that re-runs bluetoothd with <code>--noplugin=battery</code>
(the current start command is preserved, the flag is appended).</li>
<li>Reload systemd and restart the host's <code>bluetooth</code> service
(Bluetooth drops for a few seconds).</li>
</ol>
<p class="small">Nothing else changes, and removing the file restores the
original behavior. Android pairing doesn't need this.</p>
{iphone_block}
</div>
"""

BT_APPLIED_LINE = """
<p>Host Bluetooth fix: <b class="ok">applied &#10003;</b>
<span class="small">(original config backed up)</span></p>
<form method="post" action="/bluetooth/restore" style="margin-top:0.4rem;"
      onsubmit="return confirm('Restore the original Bluetooth config and restart the bluetooth service? Pairing from Android will stop working until the fix is re-applied.')">
<button type="submit" class="danger">Restore original Bluetooth config</button>
</form>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, code=200):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def _render(self, body, head_extra="", page="home"):
        """Wrap a body in the app shell: sticky header, burger menu, X."""
        host = (self.headers.get("Host") or "").split(":")[0] or "umbrel.local"
        return PAGE.format(
            body=body,
            head_extra=head_extra,
            umbrel_url="//%s/" % host,
            home_active="active" if page == "home" else "",
            bluetooth_active="active" if page == "bluetooth" else "",
            permissions_active="active" if page == "permissions" else "",
            integrations_active="active" if page == "integrations" else "",
        )

    def _redirect_page(self, form):
        page = form.get("page", "home")
        if page not in ("home", "permissions", "integrations", "bluetooth"):
            page = "home"
        return self._redirect("/bluetooth" if page == "bluetooth"
                              else "/" if page == "home" else "/?page=" + page)

    def _read_post(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_POST_BYTES:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return dict(urllib.parse.parse_qsl(raw))

    def _bt_banner_and_line(self):
        """Banner (top of page) + status lines (in the status card) for the
        Bluetooth MTU fix and the iPhone fix, plus a head-extra for
        auto-refresh while a fix request is being applied by the root
        entrypoint. Home therefore always reflects whether Bluetooth
        setup is complete."""
        mtu = mtu_status()
        bt = bt_fix_state()
        head_extra = ""
        banner = ""
        line = ""

        # iPhone fix status line: always shown so the status card reflects
        # the full Bluetooth setup state, not just the MTU fix.
        ip = bt_iphone_state()
        if ip["pending"]:
            head_extra = '<meta http-equiv="refresh" content="5">'
            iphone_line = ('<p><b class="warn">Applying the iPhone fix&hellip;</b> '
                           'this takes about 20 seconds; this page refreshes '
                           'automatically.</p>')
        elif ip["applied"]:
            iphone_line = ('<p>iPhone fix: <b class="ok">applied &#10003;</b> '
                           '<span class="small">(bluetoothd runs without the '
                           'battery plugin)</span></p>')
        else:
            iphone_line = ('<p>iPhone fix: <span class="small">not applied</span> '
                           '&mdash; <a href="/bluetooth">set up</a> '
                           '<span class="small">(only needed when pairing from an '
                           'iPhone)</span></p>')

        def result_card():
            r = bt["result"]
            if not r:
                return ""
            cls = "ok" if r.get("ok") else "no"
            when = time.strftime("%H:%M", time.localtime(r.get("at", 0)))
            return ('<div class="card"><p>Bluetooth fix (%s): <b class="%s">%s</b></p>'
                    '<p class="small">%s</p></div>'
                    % (html.escape(str(r.get("action", ""))), cls,
                       "done" if r.get("ok") else "failed",
                       html.escape(str(r.get("message", ""))) + f' <span class="small">({when})</span>'))

        if bt["pending"]:
            head_extra = '<meta http-equiv="refresh" content="5">'
            banner = ('<div class="card"><p><b class="warn">Applying the Bluetooth '
                        'fix&hellip;</b> this takes about 20 seconds; this page '
                        'refreshes automatically.</p></div>')
            line = iphone_line
        elif mtu is False:
            banner = BT_BANNER + result_card()
            line = iphone_line
        elif mtu is True:
            if bt["has_backup"]:
                line = BT_APPLIED_LINE + result_card() + iphone_line
            else:
                line = ('<p class="small">Host Bluetooth MTU: '
                        '<b class="ok">256 &#10003;</b> (set manually on the host)</p>'
                        + iphone_line)
        return head_extra, banner, line

    def _docker_html(self, page="home"):
        """Toggle card for Docker access. Returns (html, wants_refresh)."""
        st = docker_access_state()
        if st["pending"]:
            return ('<p>Full Docker access: <b class="warn">changing&hellip;</b> '
                    'the app&apos;s processes are restarting; this takes a few seconds.</p>',
                    True)
        on = st["enabled"]
        out = (
            '<p>Full Docker access: <b class="%s">%s</b></p>'
            '<p class="small"><b>Can:</b> list, inspect, stop, start and restart your other '
            'apps&apos; containers; read their logs; run commands inside them; install or '
            'remove Umbrel apps; launch new containers. Inspecting a container reveals its '
            'configuration and environment variables &mdash; where apps keep secrets like '
            'API keys and database passwords. A privileged container is effectively full '
            'access to the Pi itself.</p>'
            '<p class="small"><b>Can&apos;t:</b> anything needing you in person &mdash; '
            'entering passwords, tapping 2FA approvals on your phone &mdash; or reach into '
            'your other devices like your phone or laptop. Muse only acts when you ask it '
            'to in chat.</p>'
            '<p class="small"><b>Privacy:</b> when you ask Muse to look at an app, that '
            'app&apos;s logs and settings pass through the chat. Logs can hold personal '
            'details (file names, media titles, activity); inspecting can surface an '
            'app&apos;s secrets. The per-app key toggles on the Integrations page do not '
            'limit this &mdash; Docker access can read those key files regardless. Turning '
            'this off unplugs Docker within seconds; none of the above is possible until '
            'you turn it back on.</p>'
            '<form method="post" action="/docker-toggle">'
            '<input type="hidden" name="page" value="%s">'
            '<button type="submit">%s Docker access</button></form>'
            % ("ok" if on else "no", "enabled" if on else "disabled", page,
               "Disable" if on else "Enable"))
        if st["result"]:
            out += '<p class="small">%s</p>' % html.escape(str(st["result"].get("message", "")))
        return out, False

    def _integrations_html(self, page="home"):
        parts = []
        for st in integ.all_states():
            app = st["app"]
            if not st["has_key"]:
                badge = '<b class="no">not set up</b>'
            elif not st["enabled"]:
                badge = '<b class="no">disabled</b>'
            else:
                lt = st.get("last_test") or {}
                if lt.get("ok"):
                    badge = '<b class="ok">connected</b>'
                else:
                    badge = '<b class="warn">key saved</b>'
            block = [f'<details class="app"><summary>{html.escape(st["label"])} — {badge}</summary>']
            lt = st.get("last_test")
            if lt:
                when = time.strftime("%H:%M", time.localtime(lt.get("at", 0)))
                cls = "ok" if lt.get("ok") else "no"
                block.append(f'<p class="small">Last test ({when}): '
                             f'<b class="{cls}">{html.escape(str(lt.get("message", "")))}</b></p>')
            block.append(f'''<form method="post" action="/integrations/set">
<input type="hidden" name="page" value="{page}">
<input type="hidden" name="app" value="{app}">
<input type="text" name="base_url" placeholder="Base URL, e.g. {html.escape(st["example_base_url"])}"
       value="{html.escape(st["base_url"])}" autocapitalize="off" spellcheck="false">''')
            if st["needs_username"]:
                block.append(
                    f'<input type="text" name="username" placeholder="{html.escape(st["username_label"])}" '
                    f'value="{html.escape(st["username"])}" autocapitalize="off" spellcheck="false">')
            block.append(
                f'<input type="password" name="key" placeholder="{html.escape(st["key_label"])}'
                f'{" — leave blank to keep the saved one" if st["has_key"] else ""}" '
                f'autocomplete="off" autocapitalize="off" spellcheck="false">')
            block.append('<button type="submit">Save</button></form>')
            block.append(f'''<form method="post" action="/integrations/toggle" style="display:inline">
<input type="hidden" name="page" value="{page}">
<input type="hidden" name="app" value="{app}">
<button type="submit">{"Disable" if st["enabled"] else "Enable"}</button></form>''')
            if st["has_key"]:
                block.append(f''' <form method="post" action="/integrations/test" style="display:inline">
<input type="hidden" name="page" value="{page}">
<input type="hidden" name="app" value="{app}">
<button type="submit">Test connection</button></form>''')
                block.append(f''' <form method="post" action="/integrations/remove" style="display:inline"
onsubmit="return confirm('Remove the saved {html.escape(st["label"])} key? Muse will lose API access to it.')">
<input type="hidden" name="page" value="{page}">
<input type="hidden" name="app" value="{app}">
<button type="submit" class="danger">Remove</button></form>''')
            block.append(f'<p class="small">{html.escape(st["key_help"])}</p></details>')
            parts.append("\n".join(block))
        parts.append('''<details class="app"><summary>Add a custom integration</summary>
<p class="small">For any other app or service with an HTTP API: give it a name,
tell Muse how to authenticate, and it appears above with the same toggle,
test and remove controls.</p>
<form method="post" action="/integrations/add-custom">
<input type="hidden" name="page" value="{page}">
<input type="text" name="label" placeholder="Name, e.g. My Service" autocapitalize="off" spellcheck="false">
<input type="text" name="base_url" placeholder="Base URL, e.g. http://umbrel.local:8080" autocapitalize="off" spellcheck="false">
<select name="auth_scheme">
<option value="bearer">Bearer token (Authorization: Bearer)</option>
<option value="header">Custom header</option>
<option value="token">Token (Authorization: Token)</option>
<option value="basic">Username + password (Basic)</option>
<option value="query">Query parameter (?api_key=…)</option>
<option value="none">No authentication</option>
</select>
<input type="text" name="header" placeholder="Header name (for Custom header), e.g. X-Api-Key" autocapitalize="off" spellcheck="false">
<input type="text" name="param" placeholder="Parameter name (for Query parameter), e.g. api_key" autocapitalize="off" spellcheck="false">
<input type="text" name="username" placeholder="Username (for Basic only)" autocapitalize="off" spellcheck="false">
<input type="text" name="test_path" placeholder="Test path, e.g. /api/health (default /)" autocapitalize="off" spellcheck="false">
<input type="password" name="key" placeholder="API key / token (blank if none needed)" autocomplete="off" autocapitalize="off" spellcheck="false">
<button type="submit">Add custom integration</button></form></details>''')
        return "\n".join(parts)

    def _bluetooth_confirm_page(self):
        try:
            text = HOST_BT_CONF.read_text()
        except OSError:
            text = ""
        # Preview the [GATT] section if there is one.
        preview_lines, in_gatt, found = [], False, False
        for ln in text.splitlines():
            s = ln.strip()
            sec = re.match(r"\[\s*(.+?)\s*\]", s)
            if sec:
                if in_gatt:
                    break
                in_gatt = sec.group(1).upper() == "GATT"
                if in_gatt:
                    found = True
                    preview_lines.append(ln)
                continue
            if in_gatt:
                preview_lines.append(ln)
        preview = "\n".join(preview_lines).strip() if found else "(no [GATT] section yet)"
        current = ('<p class="small">Current <code>main.conf</code> Bluetooth '
                   'setting:</p><pre>%s</pre>' % html.escape(preview))
        # iPhone fix card: status line + apply/restore toggle + last result.
        ip = bt_iphone_state()
        head_extra = ""
        if ip["pending"]:
            head_extra = '<meta http-equiv="refresh" content="5">'
            iphone_block = ('<p><b class="warn">Applying the iPhone fix&hellip;</b> '
                            'this takes about 20 seconds; this page refreshes '
                            'automatically.</p>')
        else:
            if ip["applied"]:
                iphone_block = ('<p>iPhone fix: <b class="ok">applied &#10003;</b> '
                                '<span class="small">(bluetoothd runs without the '
                                'battery plugin)</span></p>'
                                '<form method="post" action="/bluetooth/iphone-restore" '
                                'style="margin-top:0.4rem;" onsubmit="return confirm('
                                "'Remove the iPhone fix and restart the bluetooth "
                                'service? Pairing from iPhone will stop working until '
                                "it is re-applied.')\">"
                                '<button type="submit" class="danger">Remove iPhone fix</button>'
                                '</form>')
            else:
                iphone_block = ('<p>iPhone fix: <b class="no">not applied</b></p>'
                                '<form method="post" action="/bluetooth/iphone-apply" '
                                'style="margin-top:0.4rem;">'
                                '<button type="submit">Apply iPhone fix</button></form>')
            r = ip["result"]
            if r:
                cls = "ok" if r.get("ok") else "no"
                when = time.strftime("%H:%M", time.localtime(r.get("at", 0)))
                iphone_block += ('<p class="small">Last run (%s): <b class="%s">%s</b> — %s</p>'
                                 % (when, cls,
                                    "done" if r.get("ok") else "failed",
                                    html.escape(str(r.get("message", "")))))
        return self._send(self._render(
            BT_CONFIRM.format(current=current, iphone_block=iphone_block),
            head_extra=head_extra, page="bluetooth"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/bluetooth":
            return self._bluetooth_confirm_page()
        if parsed.path != "/":
            return self._send("<h1>Not found</h1>", 404)
        query = urllib.parse.parse_qs(parsed.query)
        page = query.get("page", ["home"])[0]
        if page not in ("home", "permissions", "integrations"):
            page = "home"
        if token_source() is None:
            return self._send(self._render(SETUP_FORM.format(error=""),
                                           head_extra="", page=page))
        try:
            paired = (STATE_DIR / "pairing.json").exists()
        except OSError:
            paired = False
        ident = identity_info() or {}
        ble_name = ident.get("ble_name", "—")
        node_id = ident.get("node_id", "—")

        remaining = pair_window_remaining()
        proc_alive = pair_process_running()
        if remaining > 0 and proc_alive:
            until = time.strftime("%H:%M", time.localtime(time.time() + remaining))
            pair_section = (
                "<p><b class=\"ok\">Pairing is open until %s.</b></p>"
                "<p>On your phone: Muse app &rarr; Settings &rarr; Devices "
                "(Developer mode ON) &rarr; Add Device &rarr; choose</p>"
                '<p><code class="bigname">%s</code></p>' % (until, html.escape(ble_name))
            )
        elif remaining > 0 and not proc_alive:
            log_tail = "\n".join(pair_log_tail())
            pair_section = (
                "<p><b class=\"no\">The pairing window didn't stay open.</b> "
                "The Bluetooth setup process exited right away. Last output:</p>"
                "<pre>%s</pre>"
                "<p>Tap below to try again.</p>"
                '<form method="post" action="/pair">'
                f'<input type="hidden" name="page" value="{page}">'
                '<button type="submit">'
                "Retry pairing</button></form>" % html.escape(log_tail or "(no output)")
            )
        else:
            pair_section = (
                "<p>Pairing is closed. Open a 10-minute window, then add the "
                "device in the Muse app (Settings &rarr; Devices, Developer mode ON).</p>"
                '<form method="post" action="/pair">'
                f'<input type="hidden" name="page" value="{page}">'
                '<button type="submit">Start pairing</button></form>'
            )
        if paired:
            pair_section = (
                '<p><b class="ok">&#10003; This device is paired with the Muse app.</b></p>'
            ) + pair_section

        head_extra, bt_banner, bt_line = self._bt_banner_and_line()
        docker_html, docker_pending = self._docker_html(page)
        if docker_pending:
            head_extra = '<meta http-equiv="refresh" content="5">'

        running = daemon_running()
        status_card = STATUS_CARD.format(
            token_how="via token.env" if token_source() == "env" else "saved in the app",
            dcls="ok" if running else "warn",
            daemon="running" if running else "starting…",
            pcls="ok" if paired else "no",
            paired="yes" if paired else "no",
            ble=html.escape(ble_name),
            node=html.escape(node_id),
            bt_line=bt_line,
        )
        if page == "permissions":
            body = DOCKER_CARD.format(docker_html=docker_html)
        elif page == "integrations":
            body = INTEGRATIONS_CARD.format(
                integrations_html=self._integrations_html(page))
        else:
            body = (bt_banner + status_card
                    + PAIR_CARD.format(pair_section=pair_section)
                    + RESET_CARD.format(page=page))
        self._send(self._render(body, head_extra=head_extra, page=page))

    def do_POST(self):
        form = self._read_post()
        if self.path == "/setup":
            token = form.get("token", "").strip()
            if not TOKEN_RE.match(token):
                err = '<p class="no">That doesn\'t look like a token — it should start with <code>mgst_</code>.</p>'
                return self._send(self._render(SETUP_FORM.format(error=err), page="home"), 400)
            try:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                TOKEN_FILE.write_text(token + "\n")
                os.chmod(TOKEN_FILE, 0o600)
            except OSError:
                return self._send("<h1>Could not save the token.</h1>", 500)
            return self._redirect_page(form)
        if self.path == "/pair":
            token = effective_token()
            if token:
                start_pairing(token)
            return self._redirect_page(form)
        if self.path == "/bluetooth/apply":
            request_bt_fix("apply")
            return self._redirect("/bluetooth")
        if self.path == "/bluetooth/restore":
            request_bt_fix("restore")
            return self._redirect("/bluetooth")
        if self.path == "/bluetooth/iphone-apply":
            request_bt_iphone("apply")
            return self._redirect("/bluetooth")
        if self.path == "/bluetooth/iphone-restore":
            request_bt_iphone("restore")
            return self._redirect("/bluetooth")
        if self.path == "/docker-toggle":
            request_docker_toggle()
            return self._redirect_page(form)
        if self.path == "/integrations/set":
            app = form.get("app", "")
            if integ.definition_for(app):
                key = form.get("key", "").strip()
                if not key and integ.app_state(app)["has_key"]:
                    # key left blank: keep the saved one, update URL/username only
                    ok, msg = integ.update_config(app, form.get("base_url", ""),
                                                  form.get("username", ""))
                else:
                    ok, msg = integ.set_key(app, form.get("base_url", ""),
                                            key, form.get("username", ""))
                if not ok:
                    back = "/" if form.get("page", "home") == "home" else "/?page=" + form.get("page", "home")
                    return self._send(
                        self._render(f'<div class="card"><p class="no">{html.escape(msg)}</p>'
                                     f'<p><a href="{back}">Back</a></p></div>',
                                     head_extra="", page=form.get("page", "home")), 400)
            return self._redirect_page(form)
        if self.path == "/integrations/toggle":
            app = form.get("app", "")
            if integ.definition_for(app):
                st = integ.app_state(app)
                integ.set_enabled(app, not st["enabled"])
            return self._redirect_page(form)
        if self.path == "/integrations/test":
            app = form.get("app", "")
            if integ.definition_for(app):
                integ.test_connection(app)
            return self._redirect_page(form)
        if self.path == "/integrations/remove":
            app = form.get("app", "")
            if integ.definition_for(app):
                integ.remove(app)
            return self._redirect_page(form)
        if self.path == "/integrations/add-custom":
            ok, msg = integ.add_custom(
                form.get("label", ""), form.get("base_url", ""), form.get("key", ""),
                form.get("auth_scheme", "bearer"), form.get("header", ""),
                form.get("param", ""), form.get("username", ""), form.get("test_path", "/"))
            if not ok:
                back = "/" if form.get("page", "home") == "home" else "/?page=" + form.get("page", "home")
                return self._send(
                    self._render(f'<div class="card"><p class="no">{html.escape(msg)}</p>'
                                 f'<p><a href="{back}">Back</a></p></div>',
                                 head_extra="", page=form.get("page", "home")), 400)
            return self._redirect_page(form)
        if self.path == "/reset-token":
            try:
                TOKEN_FILE.unlink()
            except OSError:
                pass
            return self._redirect_page(form)
        return self._send("<h1>Not found</h1>", 404)

    def log_message(self, *args):  # keep the log quiet
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
