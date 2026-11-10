# Heirloom — Bitcoin inheritance app for Umbrel (v0.1.0, UI-first scaffold)

Heirloom is the map, the messenger, and the rehearsal for Bitcoin inheritance —
never the key holder. It keeps a vault map (multisig descriptor, key locations,
quorum), checks in with you on a schedule, and — if you stop checking in —
releases a two-part message to your chosen person: a plaintext notification,
plus an encrypted recovery guide only they can unlock with a password they
already hold.

Design spec: `../files/heirloom-spec-draft.md`

## What this build is

A UI-first scaffold. The full UI works end-to-end against a local JSON API;
delivery is a **stub** (it logs what would be sent and sends nothing) until
the delivery architecture is decided (hosted relay vs on-Pi paths).

- **Port:** 3100 (internal container port; Umbrel's `app_proxy` fronts it).
- **Stack:** single-container Node 20 app, **zero runtime dependencies** —
  plain `node:http`, no Express, no build step. Vanilla HTML/CSS/JS frontend
  served as static files.
- **Storage:** plain JSON files in the app data directory — no database server.
  - `state.json` — vault map, settings, recipient, channels, release, drill
  - `checkins.jsonl` — append-only check-in audit log
  - `delivery.jsonl` — append-only log of stubbed delivery attempts

## Run locally

```bash
cd app
PORT=3100 DATA_DIR=./data node server.js
# open http://localhost:3100
```

No install step, no build step. Node 20+ required (`node --version`).
Data is created under `DATA_DIR` on first run.

## Umbrel

- `umbrel-app.yml` — manifest (format verified against the Umbrel community
  app pattern: `manifestVersion: 1`, `app_proxy` + one service, volume
  `${APP_DATA_DIR}/data:/data`).
- `docker-compose.yml` — `app_proxy` + `web` (image `ghcr.io/thefuckisalommy/heirloom:latest`, built by GitHub Actions).
- `Dockerfile` — `node:20-alpine`, copies `server.js`, `lib/`, `public/`.
- Build locally: `docker build -t heirloom:0.1.0 .` (Docker was not available
  in the build VM, so the image has not been built or run in Docker yet —
  verify on the Pi before installing as an Umbrel app.)

Before any store submission, replace the `TODO` placeholder URLs in
`umbrel-app.yml` (website, repo, support, icon) and add gallery screenshots.

## API

All under `/api/`, JSON in/out:

| Route | Method | What it does |
|---|---|---|
| `/api/health` | GET | `{ok, version}` |
| `/api/state` | GET | Full state |
| `/api/status` | GET | Derived check-in status (on-track / warning-1 / warning-2 / grace / release-due / paused / vacation / not-started) |
| `/api/checkin` | POST | Record a check-in `{method: "app" \| "telegram"}`; auto-clears a pause |
| `/api/checkins` | GET | Check-in audit log (latest 50) |
| `/api/vault` | PUT | Vault map (descriptor, quorum, keys, derivation paths, notes, packet location) |
| `/api/settings` | PUT | Check-in interval, warnings, threshold, grace, pause code, vacation mode |
| `/api/pause` | POST | Pause releases with `{code}` (must match the pause code) |
| `/api/resume` | POST | Clear the pause |
| `/api/recipient` | PUT | Recipient name / email / phone / telegram |
| `/api/channels` | PUT | Channel toggles only — **no credentials are accepted here, ever** |
| `/api/delivery/test` | POST | **STUB** — logs a would-be send for `{channel}`, sends nothing |
| `/api/delivery/log` | GET | Stub delivery log (latest 50) |
| `/api/release/part1` | PUT | Part 1 plaintext template |
| `/api/release/preview` | GET | Part 1 rendered with current values |
| `/api/release/part2` | PUT | Store ciphertext blob `{enabled, ciphertext}` — server never sees plaintext or password |
| `/api/drill` | PUT | Fire-drill checklist state |

## Encryption (interim)

Part 2 uses WebCrypto **AES-GCM-256** with a key derived by **PBKDF2**
(250,000 iterations, SHA-256), fresh random salt (16 B) and IV (12 B) per
message. Encryption and decryption run in the browser (`public/crypto.js`);
the server only ever receives the ciphertext blob
`{v, kdf, iter, salt, iv, ct}`.

**This is interim.** The spec calls for **Argon2id**. Before real use, swap
the KDF for `argon2-browser` (WASM). The blob format carries a `kdf` field
so Argon2id blobs can be told apart from these PBKDF2 blobs.

The standalone decrypt page (`public/decrypt.html`) is fully self-contained
(inline crypto, no server needed) — it is what travels with the release
message so the heir can decrypt even if the Pi is gone.

## What's stubbed

- **Delivery** (`lib/delivery.js`): interface + logging only. `sendStub()`
  appends `{ts, channel, to, subject, bodyPreview, status: "logged-not-sent"}`
  to `delivery.jsonl` and returns success. No network calls, no credentials.
  Every channel toggle and test-send button in the UI is visibly marked STUB.
- **Delivery architecture**: undecided (hosted relay vs on-Pi paths). The
  settings exist (channel toggles, recipient) but nothing sends.

## Safety rules in this build

- **Never enter seed words.** The vault form warns, and the server refuses
  anything that looks like a 12+ word mnemonic in the descriptor field.
- **No personal information** in code, comments, or sample data — only
  obvious placeholders (`heir@example.com`, `+1 555-010-0000`).
- **No hardcoded credentials.** Delivery credentials are user-provided at
  runtime via settings (not implemented while delivery is stubbed), never
  in code.
- The app holds **ciphertext only**. The password and the Part 2 plaintext
  never leave the browser.

## What's next

1. Decide the delivery architecture (hosted relay vs on-Pi only vs relay
   default + on-Pi fallback), then replace `lib/delivery.js` with real
   senders behind the same interface.
2. Swap PBKDF2 → Argon2id (`argon2-browser` WASM) in `public/crypto.js`
   and `public/decrypt.html` before real use.
3. Decide the open spec questions: Shamir as an extra layer, recipient,
   check-in numbers, name.
4. Docker-build and install on the Pi; verify the Umbrel proxy path.
5. Gallery screenshots + hosted icon URL for store submission.
