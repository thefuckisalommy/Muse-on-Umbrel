#!/usr/bin/env python3
"""Apply or revert host Bluetooth fixes, as root.

Invoked by entrypoint.sh (which starts as root) when the unprivileged
status page drops a request file into /data/state. Never exposed to the
network directly.

Two independent fixes, each with its own request/result files:

- bt_fix.request ("apply"/"restore"): the BlueZ MTU tweak. Backs up
  /host-bluetooth/main.conf (the host's /etc/bluetooth), sets
  "ExchangeMTU = 256" under [GATT], then restarts the host's
  bluetooth.service over the system D-Bus.

- bt_iphone.request ("apply"/"restore"): the iPhone pairing fix. Writes
  /host-systemd/bluetooth.service.d/zz-musegadget.conf (the host's
  /etc/systemd/system drop-in) to restart bluetoothd without the battery
  plugin — BlueZ asks iPhones to bond for battery reads, iPhones only
  answer over a bonded link, the gadget refuses, and iOS drops pairing.
  Applies via D-Bus daemon-reload + bluetooth.service restart.
  Restore removes the drop-in.

On completion each writes JSON to its .result file and removes its
request file.
"""

import json
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

STATE = Path(os.environ.get("MUSEGADGET_STATE_DIR", "/data/state"))
HOST_CONF = Path("/host-bluetooth/main.conf")
REQUEST = STATE / "bt_fix.request"
RESULT = STATE / "bt_fix.result"
BACKUP = STATE / "main.conf.bak"
BACKUP_META = STATE / "main.conf.bak.meta"

HOST_SYSTEMD = Path("/host-systemd")
IPHONE_DROPIN = HOST_SYSTEMD / "bluetooth.service.d" / "zz-musegadget.conf"
IPHONE_REQUEST = STATE / "bt_iphone.request"
IPHONE_RESULT = STATE / "bt_iphone.result"
IPHONE_MARKER = "# Written by the Muse Gadget app (iPhone pairing fix)."


def set_mtu(text):
    """Return (new_text, changed). Minimal line-based edit: sets
    ExchangeMTU = 256 inside [GATT], preserving comments and all other
    content. Appends a [GATT] section if none exists."""
    lines = text.splitlines()
    out, in_gatt, gatt_seen, found = [], False, False, False
    changed = False
    for line in lines:
        s = line.strip()
        sec = re.match(r"\[\s*(.+?)\s*\]", s)
        if sec:
            if in_gatt and not found:
                out.append("ExchangeMTU = 256")
                changed = True
                found = True
            in_gatt = sec.group(1).upper() == "GATT"
            if in_gatt:
                gatt_seen = True
            out.append(line)
            continue
        if (in_gatt and not s.startswith(("#", ";"))
                and re.match(r"(?i)ExchangeMTU\s*=", s)):
            found = True
            if re.match(r"(?i)ExchangeMTU\s*=\s*256\s*$", s):
                out.append(line)
            else:
                out.append("ExchangeMTU = 256")
                changed = True
            continue
        out.append(line)
    if in_gatt and not found:
        out.append("ExchangeMTU = 256")
        changed = True
    elif not gatt_seen:
        if out and out[-1].strip() != "":
            out.append("")
        out.append("[GATT]")
        out.append("ExchangeMTU = 256")
        changed = True
    return "\n".join(out) + "\n", changed


def restart_bluetooth():
    """Restart the host's bluetooth.service over the system D-Bus."""
    import dbus

    bus = dbus.SystemBus()
    mgr = dbus.Interface(
        bus.get_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1"),
        "org.freedesktop.systemd1.Manager",
    )
    mgr.RestartUnit("bluetooth.service", "replace")


def write_result(result_path, action, ok, message):
    try:
        result_path.write_text(json.dumps({
            "action": action,
            "ok": ok,
            "message": message,
            "at": int(time.time()),
        }) + "\n")
        try:
            shutil.chown(result_path, user="gadget", group="gadget")
        except Exception:
            pass
        os.chmod(result_path, 0o644)
    except OSError as e:
        print(f"bt_fix: cannot write result: {e}", file=sys.stderr)


def do_apply():
    existed = HOST_CONF.exists()
    original = HOST_CONF.read_text() if existed else ""
    new_text, changed = set_mtu(original)
    if not changed:
        return True, "ExchangeMTU = 256 is already set — nothing changed."
    # Backup once; never overwrite an existing backup.
    if not BACKUP.exists():
        BACKUP.write_text(original)
        BACKUP_META.write_text(json.dumps({"existed": existed}) + "\n")
        try:
            shutil.chown(BACKUP, user="gadget", group="gadget")
            shutil.chown(BACKUP_META, user="gadget", group="gadget")
        except Exception:
            pass
    HOST_CONF.write_text(new_text)
    try:
        restart_bluetooth()
        restarted = True
        restart_note = "bluetooth service restarted."
    except Exception as e:  # noqa: BLE001 - surfaced to the user
        restarted = False
        restart_note = (f"config updated but the bluetooth service could not be "
                        f"restarted automatically ({e}); run "
                        f"`sudo systemctl restart bluetooth` on the host.")
    # Verify the file now reads as fixed.
    ok = False
    try:
        for line in HOST_CONF.read_text().splitlines():
            s = line.strip()
            if (s and not s.startswith(("#", ";"))
                    and re.match(r"(?i)ExchangeMTU\s*=\s*256\s*$", s)):
                ok = True
                break
    except OSError:
        pass
    if ok and restarted:
        return True, ("Fix applied: original config backed up, "
                      "ExchangeMTU = 256 set under [GATT], bluetooth restarted. "
                      "You can now pair from the Muse app.")
    if ok:
        return True, "Fix applied to the config file. " + restart_note
    return False, "Wrote the config but verification failed — " + restart_note


def do_restore():
    if not BACKUP.exists():
        return False, "No backup found — nothing to restore."
    try:
        meta = json.loads(BACKUP_META.read_text())
    except Exception:
        meta = {"existed": True}
    if meta.get("existed"):
        shutil.copy2(BACKUP, HOST_CONF)
    else:
        try:
            HOST_CONF.unlink()
        except OSError:
            pass
    try:
        restart_bluetooth()
        note = "bluetooth service restarted."
    except Exception as e:  # noqa: BLE001 - surfaced to the user
        note = (f"config restored but the bluetooth service could not be "
                f"restarted automatically ({e}); run "
                f"`sudo systemctl restart bluetooth` on the host.")
    return True, "Original Bluetooth config restored. " + note


def _systemd_manager():
    """The host's systemd manager over the system D-Bus."""
    import dbus

    bus = dbus.SystemBus()
    return dbus.Interface(
        bus.get_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1"),
        "org.freedesktop.systemd1.Manager",
    )


def _bluetooth_argv():
    """bluetooth.service's current start command (binary + args) from the
    host's systemd. Returns a list of strings, or None if unreadable."""
    import dbus

    bus = dbus.SystemBus()
    mgr = dbus.Interface(
        bus.get_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1"),
        "org.freedesktop.systemd1.Manager",
    )
    try:
        unit_path = mgr.GetUnit("bluetooth.service")
    except Exception:
        return None
    svc = bus.get_object("org.freedesktop.systemd1", unit_path)
    props = dbus.Interface(svc, "org.freedesktop.DBus.Properties")
    try:
        exec_start = props.Get("org.freedesktop.systemd1.Service", "ExecStart")
    except Exception:
        return None
    if not exec_start:
        return None
    return [str(a) for a in exec_start[0][1]]


def _systemd_reload_and_restart_bluetooth():
    mgr = _systemd_manager()
    mgr.Reload()  # daemon-reload so the drop-in is picked up
    mgr.RestartUnit("bluetooth.service", "replace")


def do_apply_iphone():
    try:
        if IPHONE_DROPIN.exists():
            try:
                if IPHONE_MARKER in IPHONE_DROPIN.read_text():
                    return True, "iPhone fix is already applied — nothing changed."
            except OSError:
                pass
            return False, (f"{IPHONE_DROPIN} already exists but wasn't written "
                           "by this app — refusing to overwrite it. Remove it "
                           "by hand if you're sure.")
    except OSError as e:
        return False, f"Could not check the systemd drop-in dir: {e}"
    argv = _bluetooth_argv()
    if not argv:
        return False, ("Could not read bluetooth.service's start command from "
                       "the host — use the manual steps in the setup guide "
                       "instead.")
    if "--noplugin=battery" in argv:
        return True, ("bluetoothd already runs without the battery plugin — "
                      "nothing changed.")
    argv = argv + ["--noplugin=battery"]
    dropin = (IPHONE_MARKER + "\n"
              "# BlueZ's battery plugin asks iPhones to bond for battery reads;\n"
              "# iPhones only answer over a bonded link, the gadget refuses, and\n"
              "# iOS drops the pairing. Running without the plugin avoids that.\n"
              "# Remove this file (or use Restore in the app) to go back.\n"
              "[Service]\n"
              "ExecStart=\n"
              "ExecStart=" + shlex.join(argv) + "\n")
    try:
        IPHONE_DROPIN.parent.mkdir(parents=True, exist_ok=True)
        IPHONE_DROPIN.write_text(dropin)
    except OSError as e:
        return False, f"Could not write the systemd drop-in: {e}"
    try:
        _systemd_reload_and_restart_bluetooth()
    except Exception as e:  # noqa: BLE001 - surfaced to the user
        return False, (f"Drop-in written, but bluetooth could not be "
                       f"reloaded/restarted automatically ({e}); run "
                       f"`sudo systemctl daemon-reload && sudo systemctl "
                       f"restart bluetooth` on the host.")
    return True, ("iPhone fix applied: bluetoothd now starts with "
                  "--noplugin=battery and the bluetooth service was restarted. "
                  "Pairing from iPhone should work now.")


def do_restore_iphone():
    try:
        exists = IPHONE_DROPIN.exists()
    except OSError as e:
        return False, f"Could not check the systemd drop-in dir: {e}"
    if not exists:
        return True, "iPhone fix isn't applied — nothing to restore."
    try:
        IPHONE_DROPIN.unlink()
        try:
            IPHONE_DROPIN.parent.rmdir()  # only removes it if we left it empty
        except OSError:
            pass
    except OSError as e:
        return False, f"Could not remove the drop-in: {e}"
    try:
        _systemd_reload_and_restart_bluetooth()
    except Exception as e:  # noqa: BLE001 - surfaced to the user
        return False, (f"Drop-in removed, but bluetooth could not be "
                       f"reloaded/restarted automatically ({e}); run "
                       f"`sudo systemctl daemon-reload && sudo systemctl "
                       f"restart bluetooth` on the host.")
    return True, ("iPhone fix removed: bluetoothd is back to its original "
                  "start command and the bluetooth service was restarted.")


def _process(request_path, result_path, actions):
    """Handle one pending request file. Returns True if one was processed."""
    try:
        action = request_path.read_text().strip().lower()
    except OSError:
        return False  # no request pending
    try:
        fn = actions.get(action)
        if fn is None:
            ok, message = False, f"Unknown request: {action!r}."
        else:
            ok, message = fn()
        write_result(result_path, action, ok, message)
    except Exception as e:  # noqa: BLE001 - never crash the supervisor loop
        try:
            write_result(result_path, action, False, f"Fix failed: {e}")
        except Exception:
            pass
    finally:
        try:
            request_path.unlink()
        except OSError:
            pass
    return True


def main():
    _process(REQUEST, RESULT,
             {"apply": do_apply, "restore": do_restore})
    _process(IPHONE_REQUEST, IPHONE_RESULT,
             {"apply": do_apply_iphone, "restore": do_restore_iphone})
    return 0


if __name__ == "__main__":
    sys.exit(main())
