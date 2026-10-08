#!/usr/bin/env python3
"""Per-app API integrations for the Muse Gadget Umbrel app.

Lets the user hand Muse API access to popular Umbrel apps (Home
Assistant, Immich, Jellyfin, Nextcloud) with a per-app on/off toggle.

- Keys are stored in /data/state/keys/<app> (mode 600, owned by the
  unprivileged gadget user). They are never printed, logged, or committed.
- Toggle state lives in /data/state/integrations.json. Disabling an app
  chmods its key file to 000, so nothing running as the gadget user —
  including Muse's own shell commands — can read it until re-enabled.
- This module never emits key material in messages or errors; callers
  must treat keys as opaque.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATE_DIR = Path(os.environ.get("MUSEGADGET_STATE_DIR", "/data/state"))
KEYS_DIR = STATE_DIR / "keys"
REGISTRY = STATE_DIR / "integrations.json"

APPS = {
    "homeassistant": {
        "label": "Home Assistant",
        "key_label": "Long-lived access token",
        "key_help": "Home Assistant → click your profile → Long-lived access tokens → Create token",
        "needs_username": False,
        "test_method": "GET",
        "test_path": "/api/",
        "auth": "bearer",  # Authorization: Bearer <key>
        "example_base_url": "http://homeassistant.local:8123",
    },
    "immich": {
        "label": "Immich",
        "key_label": "API key",
        "key_help": "Immich → Account settings → API keys → New API key",
        "needs_username": False,
        "test_method": "GET",
        "test_path": "/api/server-info",
        "auth": "header",
        "header": "x-api-key",
        "example_base_url": "http://umbrel.local:2283",
    },
    "jellyfin": {
        "label": "Jellyfin",
        "key_label": "API key",
        "key_help": "Jellyfin → Dashboard → API keys → New API key",
        "needs_username": False,
        "test_method": "GET",
        "test_path": "/System/Info",
        "auth": "header",
        "header": "X-Emby-Token",
        "example_base_url": "http://umbrel.local:8096",
    },
    "nextcloud": {
        "label": "Nextcloud",
        "key_label": "App password",
        "key_help": "Nextcloud → Settings → Security → Create new app password",
        "needs_username": True,
        "username_label": "Username",
        "test_method": "GET",
        "test_path": "/ocs/v1.php/cloud/capabilities",
        "auth": "basic",  # Basic base64(user:key)
        "extra_headers": {"OCS-APIRequest": "true"},
        "example_base_url": "http://umbrel.local:8080",
    },
    "paperless": {
        "label": "Paperless-ngx",
        "key_label": "API token",
        "key_help": "Paperless-ngx → Profile (top right) → API tokens → Create token",
        "needs_username": False,
        "test_method": "GET",
        "test_path": "/api/documents/",
        "auth": "token",  # Authorization: Token <key>
        "example_base_url": "http://umbrel.local:8000",
    },
    "audiobookshelf": {
        "label": "Audiobookshelf",
        "key_label": "API token",
        "key_help": "Audiobookshelf → Settings → Users → click your user → API tokens → Create",
        "needs_username": False,
        "test_method": "GET",
        "test_path": "/api/libraries",
        "auth": "bearer",
        "example_base_url": "http://umbrel.local:13378",
    },
    "mealie": {
        "label": "Mealie",
        "key_label": "API token",
        "key_help": "Mealie → click your profile → Manage API tokens → New token",
        "needs_username": False,
        "test_method": "GET",
        "test_path": "/api/app/about",
        "auth": "bearer",
        "example_base_url": "http://umbrel.local:9000",
    },
    "n8n": {
        "label": "n8n",
        "key_label": "API key",
        "key_help": "n8n → Settings → n8n API → Create an API key",
        "needs_username": False,
        "test_method": "GET",
        "test_path": "/api/v1/workflows",
        "auth": "header",
        "header": "X-N8N-API-KEY",
        "example_base_url": "http://umbrel.local:5678",
    },
    "syncthing": {
        "label": "Syncthing",
        "key_label": "API key",
        "key_help": "Syncthing → Actions → Settings → GUI → API key (or generate one)",
        "needs_username": False,
        "test_method": "GET",
        "test_path": "/rest/system/status",
        "auth": "header",
        "header": "X-API-Key",
        "example_base_url": "http://umbrel.local:8384",
    },
    "sonarr": {
        "label": "Sonarr",
        "key_label": "API key",
        "key_help": "Sonarr → Settings → General → API Key",
        "needs_username": False,
        "test_method": "GET",
        "test_path": "/api/v3/system/status",
        "auth": "header",
        "header": "X-Api-Key",
        "example_base_url": "http://umbrel.local:8989",
    },
    "radarr": {
        "label": "Radarr",
        "key_label": "API key",
        "key_help": "Radarr → Settings → General → API Key",
        "needs_username": False,
        "test_method": "GET",
        "test_path": "/api/v3/system/status",
        "auth": "header",
        "header": "X-Api-Key",
        "example_base_url": "http://umbrel.local:7878",
    },
    "jellyseerr": {
        "label": "Jellyseerr",
        "key_label": "API key",
        "key_help": "Jellyseerr → Settings → General → API key",
        "needs_username": False,
        "test_method": "GET",
        "test_path": "/api/v1/status",
        "auth": "header",
        "header": "X-Api-Key",
        "example_base_url": "http://umbrel.local:5055",
    },
}

KEY_RE = {
    # loose sanity checks so obvious paste mistakes get rejected early
    "homeassistant": r".{8,}",
    "immich": r".{8,}",
    "jellyfin": r".{8,}",
    "nextcloud": r".{4,}",
}


def definition_for(app):
    """Static definition for a built-in app, or the stored definition for
    a user-added custom integration (registry id "custom:<slug>")."""
    if app in APPS:
        return APPS[app]
    entry = load_registry().get(app, {})
    if not entry.get("custom"):
        return None
    scheme = entry.get("auth_scheme", "bearer")
    return {
        "label": entry.get("label", app),
        "key_label": "API key / token",
        "key_help": "Custom integration — paste the credential this service expects.",
        "needs_username": scheme == "basic",
        "username_label": "Username",
        "auth": scheme,
        "header": entry.get("header", "X-Api-Key"),
        "param": entry.get("param", "api_key"),
        "test_method": "GET",
        "test_path": entry.get("test_path", "/"),
        "extra_headers": {},
        "example_base_url": entry.get("base_url", ""),
        "custom": True,
    }


def known_apps():
    """All app ids: built-ins plus user-added custom integrations."""
    reg = load_registry()
    customs = [k for k in reg if k.startswith("custom:") and reg[k].get("custom")]
    return list(APPS) + customs


def load_registry():
    try:
        reg = json.loads(REGISTRY.read_text())
        return reg if isinstance(reg, dict) else {}
    except Exception:
        return {}


def save_registry(reg):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2) + "\n")
    os.chmod(REGISTRY, 0o600)


def key_path(app):
    return KEYS_DIR / app


def app_state(app):
    """Merged definition + stored state for one app (built-in or custom)."""
    definition = definition_for(app)
    if not definition:
        return None
    stored = load_registry().get(app, {})
    try:
        has_key = key_path(app).stat().st_size > 0
    except OSError:
        has_key = False
    if definition.get("auth") == "none":
        # no credential needed: having a base URL counts as set up
        has_key = bool(stored.get("base_url"))
    return {
        "app": app,
        "label": definition["label"],
        "key_label": definition["key_label"],
        "key_help": definition["key_help"],
        "needs_username": definition.get("needs_username", False),
        "username_label": definition.get("username_label", "Username"),
        "example_base_url": definition.get("example_base_url", "http://umbrel.local"),
        "base_url": stored.get("base_url", ""),
        "username": stored.get("username", ""),
        "enabled": stored.get("enabled", True),
        "has_key": has_key,
        "last_test": stored.get("last_test"),
        "custom": definition.get("custom", False),
    }


def all_states():
    return [app_state(app) for app in known_apps()]


def _valid_key_shape(app, key):
    import re

    return re.fullmatch(KEY_RE.get(app, r".{4,}"), key or "")


def set_key(app, base_url, key, username=""):
    """Store (or replace) an app's API key. Enables the app unless it was
    explicitly disabled before."""
    definition = definition_for(app)
    if not definition:
        return False, "Unknown app."
    if not _valid_key_shape(app, key):
        return False, "That doesn't look like a valid key — check for a paste mistake."
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        return False, "Base URL must start with http:// or https://."
    try:
        KEYS_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(KEYS_DIR, 0o700)
        key_path(app).write_text(key.strip() + "\n")
        os.chmod(key_path(app), 0o600)
    except OSError:
        return False, "Could not save the key."
    reg = load_registry()
    entry = reg.get(app, {})
    entry.update({
        "base_url": base_url,
        "has_key": True,
        # keep an explicit disable; otherwise a fresh key enables the app
        "enabled": entry.get("enabled", True),
    })
    if definition.get("needs_username"):
        entry["username"] = (username or "").strip()
    reg[app] = entry
    save_registry(reg)
    try:
        os.chmod(key_path(app), 0o600 if entry["enabled"] else 0o000)
    except OSError:
        pass
    return True, "Key saved."


def update_config(app, base_url, username=""):
    """Update base URL / username without touching the saved key."""
    definition = definition_for(app)
    if not definition:
        return False, "Unknown app."
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        return False, "Base URL must start with http:// or https://."
    reg = load_registry()
    entry = reg.get(app, {})
    entry["base_url"] = base_url
    if definition.get("needs_username"):
        entry["username"] = (username or "").strip()
    reg[app] = entry
    save_registry(reg)
    return True, "Saved."


def set_enabled(app, enabled):
    if not definition_for(app):
        return False, "Unknown app."
    reg = load_registry()
    entry = reg.get(app, {})
    entry["enabled"] = bool(enabled)
    reg[app] = entry
    save_registry(reg)
    # Real enforcement: an unreadable key cannot be used by anyone,
    # including Muse's own shell commands on this device.
    try:
        os.chmod(key_path(app), 0o600 if enabled else 0o000)
    except OSError:
        pass
    return True, "Enabled." if enabled else "Disabled."


def remove(app):
    definition = definition_for(app)
    if not definition:
        return False, "Unknown app."
    try:
        key_path(app).unlink()
    except OSError:
        pass
    reg = load_registry()
    reg.pop(app, None)
    save_registry(reg)
    return True, "Removed."


def add_custom(label, base_url, key, auth_scheme, header="", param="",
              username="", test_path="/"):
    """Add a user-defined custom integration. Returns (ok, message)."""
    import re

    label = (label or "").strip()
    if not label:
        return False, "Give the integration a name."
    if auth_scheme not in ("bearer", "header", "token", "basic", "query", "none"):
        return False, "Unknown auth type."
    slug = "custom:" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    if not slug or slug == "custom:":
        return False, "That name doesn't make a usable id."
    reg = load_registry()
    existed_before = slug in reg or slug in APPS
    if existed_before:
        return False, "An integration with that name already exists."
    test_path = (test_path or "/").strip() or "/"
    if not test_path.startswith("/"):
        test_path = "/" + test_path
    entry = {
        "custom": True,
        "label": label,
        "base_url": "",  # set below via set_key/update_config validation
        "auth_scheme": auth_scheme,
        "header": (header or "X-Api-Key").strip(),
        "param": (param or "api_key").strip(),
        "test_path": test_path,
        "enabled": True,
    }
    reg[slug] = entry
    save_registry(reg)
    if auth_scheme == "none" or not key.strip():
        # no key needed / provided: just store the config
        ok, msg = update_config(slug, base_url, username)
    else:
        ok, msg = set_key(slug, base_url, key, username)
    if not ok and not existed_before:
        # roll back the entry we just created (never anything pre-existing)
        reg = load_registry()
        reg.pop(slug, None)
        save_registry(reg)
    return ok, msg


def _auth_headers(definition, key, username=""):
    """Build auth headers for an app definition. The 'query' scheme is
    handled by the caller (key goes in the URL, not a header)."""
    scheme = definition.get("auth", "bearer")
    if scheme == "bearer":
        return {"Authorization": "Bearer " + key}
    if scheme == "header":
        name = definition.get("header", "X-Api-Key")
        return {name: key}
    if scheme == "token":
        return {"Authorization": "Token " + key}
    if scheme == "basic":
        token = base64.b64encode(f"{username}:{key}".encode()).decode()
        return {"Authorization": "Basic " + token}
    return {}


def _test_url(definition, base_url, key):
    url = base_url + definition.get("test_path", "/")
    if definition.get("auth") == "query":
        param = definition.get("param", "api_key")
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.quote(param)}={urllib.parse.quote(key)}"
    return url


def test_connection(app):
    """Probe the app's API with the stored key. Returns (ok, message) —
    the message never contains key material."""
    definition = definition_for(app)
    st = app_state(app)
    if not definition or not st:
        return False, "Unknown app."
    if not st["has_key"]:
        return False, "No key saved yet."
    if not st["enabled"]:
        return False, "This integration is disabled."
    if not st["base_url"]:
        return False, "No base URL saved yet."
    key = ""
    if definition.get("auth") != "none":
        try:
            key = key_path(app).read_text().strip()
        except OSError:
            return False, "Could not read the saved key."
    url = _test_url(definition, st["base_url"], key)
    headers = _auth_headers(definition, key, st["username"])
    headers.update(definition.get("extra_headers", {}))
    req = urllib.request.Request(url, headers=headers,
                                 method=definition.get("test_method", "GET"))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
            msg = f"OK (HTTP {resp.status})" if ok else f"Unexpected HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        ok, msg = False, f"HTTP {e.code} — the key was rejected or the path is wrong."
    except urllib.error.URLError as e:
        ok, msg = False, f"Could not reach {st['base_url']}: {e.reason}."
    except Exception as e:  # noqa: BLE001 - reported, never raised
        ok, msg = False, f"Connection failed: {e}."
    reg = load_registry()
    entry = reg.get(app, {})
    entry["last_test"] = {"ok": ok, "at": int(time.time()), "message": msg}
    reg[app] = entry
    save_registry(reg)
    return ok, msg
