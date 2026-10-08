# App integrations — agent guide

The Muse Gadget Umbrel app can hold API keys for the user's other Umbrel
apps, each with an individual on/off toggle in the web UI ("App
integrations" card). This is how Muse acts *inside* those apps (as opposed
to Docker-level container management, which is a separate toggle).

## Where things live (on the Pi, as the `gadget` user)

- Registry: `/data/state/integrations.json`
  ```json
  {"immich": {"base_url": "http://umbrel.local:2283", "enabled": true,
              "has_key": true,
              "last_test": {"ok": true, "at": 1234567890, "message": "OK (HTTP 200)"}}}
  ```
- Keys: `/data/state/keys/<app>` — mode `600` when enabled, `000` when
  disabled. A disabled app's key is genuinely unreadable.

## Rules

1. **Check the toggle first.** Read the registry; if `enabled` is not true,
   refuse the task and say the integration is disabled. Do not work around it.
2. **Never print, log, or repeat a key** — in chat, in files, in memory, in
   commit messages. Use it only inside the shell command that needs it, read
   from the key file: `$(cat /data/state/keys/immich)`.
3. **Never store keys anywhere else.** Not in chat, not in git, not in
   `~/MEMORY.md`.
4. Base URLs are LAN addresses; the Pi reaches them directly.

## Per-app auth

| App | Key type | Header | Test endpoint |
|-----|----------|--------|---------------|
| Home Assistant | Long-lived access token | `Authorization: Bearer <key>` | `GET {base}/api/` |
| Immich | API key | `x-api-key: <key>` | `GET {base}/api/server-info` |
| Jellyfin | API key | `X-Emby-Token: <key>` | `GET {base}/System/Info` |
| Nextcloud | App password (+ username in registry) | `Authorization: Basic base64(user:key)` + `OCS-APIRequest: true` | `GET {base}/ocs/v1.php/cloud/capabilities` |
| Paperless-ngx | API token | `Authorization: Token <key>` | `GET {base}/api/documents/` |
| Audiobookshelf | API token | `Authorization: Bearer <key>` | `GET {base}/api/libraries` |
| Mealie | API token | `Authorization: Bearer <key>` | `GET {base}/api/app/about` |
| n8n | API key | `X-N8N-API-KEY: <key>` | `GET {base}/api/v1/workflows` |
| Syncthing | API key | `X-API-Key: <key>` | `GET {base}/rest/system/status` |
| Sonarr / Radarr | API key | `X-Api-Key: <key>` | `GET {base}/api/v3/system/status` |
| Jellyseerr | API key | `X-Api-Key: <key>` | `GET {base}/api/v1/status` |
| Custom | per the user's auth-style choice | bearer / custom header / `Token` / Basic / query param / none | user-supplied test path |

Custom integrations live in the registry under `custom:<slug>` with their
auth config inline; `definition_for()` builds a definition from it, so the
same code paths (toggle, test, remove) work unchanged.

## Example calls (run on the Pi via `system.run`)

```bash
# Home Assistant: list entity states
curl -s -H "Authorization: Bearer $(cat /data/state/keys/homeassistant)" \
  "$HA_BASE/api/states" | head -c 2000
# (read base_url from the registry first)

# Immich: list albums
curl -s -H "x-api-key: $(cat /data/state/keys/immich)" \
  "$IMMICH_BASE/api/albums" | head -c 2000

# Jellyfin: server info
curl -s -H "X-Emby-Token: $(cat /data/state/keys/jellyfin)" \
  "$JELLYFIN_BASE/System/Info" | head -c 2000
```

Keep responses small (`head -c`, `jq` filters) — API payloads can be large.

## Adding a new app

1. Add an entry to `APPS` in `integrations.py` (label, key instructions,
   auth scheme, test path).
2. Document its auth row in the table above.
3. No entrypoint changes needed — key storage and toggles are handled
   entirely by the unprivileged web UI.
