'use strict';
// Heirloom — single-container Node app. ZERO runtime dependencies.
// Serves the static UI and a JSON API backed by plain files in DATA_DIR.
// Run:  PORT=3100 DATA_DIR=./data node server.js
const http = require('http');
const fs = require('fs');
const path = require('path');

const store = require('./lib/store');
const delivery = require('./lib/delivery');

const PORT = parseInt(process.env.PORT || '3100', 10);
const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, 'data');
const PUBLIC_DIR = path.join(__dirname, 'public');
const VERSION = '0.1.0';
const MAX_BODY = 1024 * 1024; // 1 MB

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.json': 'application/json; charset=utf-8',
};

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body) });
  res.end(body);
}

function sendError(res, status, message) {
  sendJson(res, status, { error: message });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) { reject(new Error('body too large')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {}); }
      catch { reject(new Error('invalid JSON')); }
    });
    req.on('error', reject);
  });
}

function clampInt(v, def, min, max) {
  const n = parseInt(v, 10);
  if (Number.isNaN(n)) return def;
  return Math.min(max, Math.max(min, n));
}

function asStr(v, maxLen) {
  if (typeof v !== 'string') return '';
  return v.slice(0, maxLen);
}

// Safety guard: refuse anything that looks like a seed phrase in the
// descriptor field. Descriptors never contain 12+ space-separated words;
// mnemonics do. (The UI also warns never to enter seed words.)
function looksLikeMnemonic(s) {
  if (!s || typeof s !== 'string') return false;
  const words = s.trim().split(/\s+/).filter((w) => /^[a-z]{3,8}$/.test(w));
  return words.length >= 12;
}

// Derived check-in status from state + now.
function checkinStatus(state, now) {
  const s = state.settings;
  const last = state.checkin.lastCheckinAt ? new Date(state.checkin.lastCheckinAt) : null;
  if (s.vacationMode) {
    return { state: 'vacation', label: 'Vacation mode', daysSince: last ? days(last, now) : null, vacationUntil: s.vacationUntil || null };
  }
  if (s.paused) {
    return { state: 'paused', label: 'Paused', daysSince: last ? days(last, now) : null };
  }
  if (!last) {
    return { state: 'not-started', label: 'Not started', daysSince: null, nextDue: null };
  }
  const d = days(last, now);
  const nextDue = new Date(last.getTime() + s.checkinIntervalDays * 86400000).toISOString();
  if (d <= s.checkinIntervalDays) return { state: 'on-track', label: 'On track', daysSince: d, nextDue };
  if (d <= s.warn2Days) return { state: 'warning-1', label: 'Check-in overdue — first warning', daysSince: d, nextDue };
  if (d <= s.thresholdDays) return { state: 'warning-2', label: 'Check-in overdue — second warning', daysSince: d, nextDue };
  if (d <= s.thresholdDays + s.graceDays) return { state: 'grace', label: 'In grace period', daysSince: d, nextDue };
  return { state: 'release-due', label: 'Release due', daysSince: d, nextDue };
}

function days(from, to) {
  return Math.floor((to - from) / 86400000);
}

function renderPart1(state) {
  const t = state.release.part1Template;
  const repl = {
    owner_name: 'the owner',
    executor_contact: state.recipient.name || '(executor not set)',
    packet_location: state.vault.packetLocation || '(packet location not set)',
    days_since: state.checkin.lastCheckinAt ? String(days(new Date(state.checkin.lastCheckinAt), new Date())) : '?',
    recipient_name: state.recipient.name || '(recipient not set)',
  };
  return t.replace(/\{\{(\w+)\}\}/g, (m, k) => (k in repl ? repl[k] : m));
}

function serveStatic(req, res, pathname) {
  let file = pathname === '/' ? 'index.html' : pathname.slice(1);
  // Prevent path traversal.
  const resolved = path.resolve(PUBLIC_DIR, file);
  if (!resolved.startsWith(PUBLIC_DIR)) { sendError(res, 403, 'forbidden'); return; }
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) { sendError(res, 404, 'not found'); return; }
  const ext = path.extname(resolved);
  res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
  fs.createReadStream(resolved).pipe(res);
}

async function handleApi(req, res, pathname) {
  const now = new Date();
  let state = store.loadState(DATA_DIR);

  // GET /api/health
  if (req.method === 'GET' && pathname === '/api/health') {
    return sendJson(res, 200, { ok: true, version: VERSION });
  }

  // GET /api/state
  if (req.method === 'GET' && pathname === '/api/state') {
    return sendJson(res, 200, state);
  }

  // GET /api/status
  if (req.method === 'GET' && pathname === '/api/status') {
    return sendJson(res, 200, {
      ...checkinStatus(state, now),
      lastCheckinAt: state.checkin.lastCheckinAt,
      totalCheckins: state.checkin.totalCheckins,
      part2Enabled: state.release.part2Enabled,
      part2Stored: !!state.release.part2,
      paused: state.settings.paused,
      vacationMode: state.settings.vacationMode,
    });
  }

  // GET /api/checkins
  if (req.method === 'GET' && pathname === '/api/checkins') {
    return sendJson(res, 200, store.readCheckins(DATA_DIR, 50).reverse());
  }

  // GET /api/delivery/log
  if (req.method === 'GET' && pathname === '/api/delivery/log') {
    return sendJson(res, 200, store.readDeliveryLog(DATA_DIR, 50).reverse());
  }

  // GET /api/release/preview — Part 1 rendered with current values
  if (req.method === 'GET' && pathname === '/api/release/preview') {
    return sendJson(res, 200, { rendered: renderPart1(state) });
  }

  // POST /api/checkin
  if (req.method === 'POST' && pathname === '/api/checkin') {
    const body = await readBody(req).catch(() => ({}));
    const method = body.method === 'telegram' ? 'telegram' : 'app';
    const wasPaused = state.settings.paused;
    state.checkin.lastCheckinAt = now.toISOString();
    state.checkin.totalCheckins += 1;
    if (wasPaused) state.settings.paused = false; // owner is alive — auto-resume
    store.saveState(DATA_DIR, state);
    store.appendCheckin(DATA_DIR, { ts: now.toISOString(), method, autoResumed: wasPaused });
    return sendJson(res, 200, { ok: true, status: checkinStatus(state, now), autoResumed: wasPaused });
  }

  // PUT /api/vault
  if (req.method === 'PUT' && pathname === '/api/vault') {
    const body = await readBody(req);
    if (body.descriptor !== undefined && looksLikeMnemonic(body.descriptor)) {
      return sendError(res, 400, 'descriptor looks like a seed phrase (12+ words) — never enter seed words. Store the descriptor only.');
    }
    const v = state.vault;
    if (body.descriptor !== undefined) v.descriptor = asStr(body.descriptor, 4000);
    if (body.quorumM !== undefined) v.quorumM = clampInt(body.quorumM, v.quorumM, 1, 20);
    if (body.quorumN !== undefined) v.quorumN = clampInt(body.quorumN, v.quorumN, 1, 20);
    if (Array.isArray(body.keys)) {
      v.keys = body.keys.slice(0, 20).map((k, i) => ({
        id: asStr(k.id, 40) || ('key-' + (i + 1)),
        label: asStr(k.label, 80) || ('Key ' + (i + 1)),
        type: asStr(k.type, 40) || 'hardware',
        location: asStr(k.location, 200),
      }));
    }
    if (body.derivationPaths !== undefined) v.derivationPaths = asStr(body.derivationPaths, 2000);
    if (body.notes !== undefined) v.notes = asStr(body.notes, 4000);
    if (body.packetLocation !== undefined) v.packetLocation = asStr(body.packetLocation, 300);
    store.saveState(DATA_DIR, state);
    return sendJson(res, 200, v);
  }

  // PUT /api/settings
  if (req.method === 'PUT' && pathname === '/api/settings') {
    const body = await readBody(req);
    const s = state.settings;
    if (body.checkinIntervalDays !== undefined) s.checkinIntervalDays = clampInt(body.checkinIntervalDays, s.checkinIntervalDays, 1, 365);
    if (body.warn1Days !== undefined) s.warn1Days = clampInt(body.warn1Days, s.warn1Days, 1, 3650);
    if (body.warn2Days !== undefined) s.warn2Days = clampInt(body.warn2Days, s.warn2Days, 1, 3650);
    if (body.thresholdDays !== undefined) s.thresholdDays = clampInt(body.thresholdDays, s.thresholdDays, 1, 3650);
    if (body.graceDays !== undefined) s.graceDays = clampInt(body.graceDays, s.graceDays, 0, 365);
    if (body.pauseCode !== undefined) s.pauseCode = asStr(body.pauseCode, 128);
    if (body.vacationMode !== undefined) s.vacationMode = !!body.vacationMode;
    if (body.vacationUntil !== undefined) s.vacationUntil = asStr(body.vacationUntil, 10);
    store.saveState(DATA_DIR, state);
    return sendJson(res, 200, s);
  }

  // POST /api/pause — trusted person pauses with the pause code
  if (req.method === 'POST' && pathname === '/api/pause') {
    const body = await readBody(req);
    if (!state.settings.pauseCode) return sendError(res, 400, 'no pause code is set — set one in Settings first');
    if (body.code !== state.settings.pauseCode) return sendError(res, 403, 'wrong pause code');
    state.settings.paused = true;
    store.saveState(DATA_DIR, state);
    return sendJson(res, 200, { ok: true, paused: true });
  }

  // POST /api/resume
  if (req.method === 'POST' && pathname === '/api/resume') {
    state.settings.paused = false;
    store.saveState(DATA_DIR, state);
    return sendJson(res, 200, { ok: true, paused: false });
  }

  // PUT /api/recipient
  if (req.method === 'PUT' && pathname === '/api/recipient') {
    const body = await readBody(req);
    const r = state.recipient;
    if (body.name !== undefined) r.name = asStr(body.name, 120);
    if (body.email !== undefined) r.email = asStr(body.email, 200);
    if (body.phone !== undefined) r.phone = asStr(body.phone, 40);
    if (body.telegram !== undefined) r.telegram = asStr(body.telegram, 80);
    store.saveState(DATA_DIR, state);
    return sendJson(res, 200, r);
  }

  // PUT /api/channels — toggles only. No credentials are accepted here, ever.
  if (req.method === 'PUT' && pathname === '/api/channels') {
    const body = await readBody(req);
    for (const ch of ['email', 'telegram', 'sms']) {
      if (body[ch] && typeof body[ch].enabled === 'boolean') state.channels[ch].enabled = body[ch].enabled;
    }
    store.saveState(DATA_DIR, state);
    return sendJson(res, 200, state.channels);
  }

  // POST /api/delivery/test — STUB: logs what would be sent, sends nothing.
  if (req.method === 'POST' && pathname === '/api/delivery/test') {
    const body = await readBody(req);
    const channel = body.channel;
    if (!delivery.isChannel(channel)) return sendError(res, 400, 'unknown channel');
    if (!state.channels[channel].enabled) return sendError(res, 400, 'channel is not enabled — enable it in Delivery settings first');
    const to = channel === 'email' ? state.recipient.email
      : channel === 'sms' ? state.recipient.phone
      : state.recipient.telegram;
    if (!to) return sendError(res, 400, 'no recipient address for this channel — fill it in under Recipient first');
    const entry = delivery.sendStub(DATA_DIR, {
      channel,
      to,
      subject: 'Heirloom test message (STUB)',
      body: renderPart1(state),
    });
    return sendJson(res, 200, { ok: true, stub: true, entry });
  }

  // PUT /api/release/part1
  if (req.method === 'PUT' && pathname === '/api/release/part1') {
    const body = await readBody(req);
    if (typeof body.template !== 'string') return sendError(res, 400, 'template must be a string');
    state.release.part1Template = body.template.slice(0, 10000);
    store.saveState(DATA_DIR, state);
    return sendJson(res, 200, { template: state.release.part1Template });
  }

  // PUT /api/release/part2 — stores the ciphertext blob ONLY.
  // The server never receives plaintext or the password.
  if (req.method === 'PUT' && pathname === '/api/release/part2') {
    const body = await readBody(req);
    state.release.part2Enabled = !!body.enabled;
    if (body.ciphertext === null || body.ciphertext === undefined) {
      state.release.part2 = null;
    } else {
      const c = body.ciphertext;
      if (typeof c !== 'object' || !c.salt || !c.iv || !c.ct || !c.kdf) {
        return sendError(res, 400, 'ciphertext must be a blob object {v, kdf, iter, salt, iv, ct}');
      }
      state.release.part2 = {
        v: 1,
        kdf: asStr(c.kdf, 40),
        iter: clampInt(c.iter, 0, 2000000),
        salt: asStr(c.salt, 100),
        iv: asStr(c.iv, 100),
        ct: asStr(c.ct, 200000),
        createdAt: now.toISOString(),
      };
    }
    store.saveState(DATA_DIR, state);
    return sendJson(res, 200, { enabled: state.release.part2Enabled, stored: !!state.release.part2 });
  }

  // PUT /api/drill
  if (req.method === 'PUT' && pathname === '/api/drill') {
    const body = await readBody(req);
    if (!Array.isArray(body.steps)) return sendError(res, 400, 'steps must be an array');
    const known = new Map(state.drill.steps.map((s) => [s.id, s]));
    for (const s of body.steps) {
      if (known.has(s.id) && typeof s.done === 'boolean') {
        const cur = known.get(s.id);
        cur.done = s.done;
        cur.doneAt = s.done ? (cur.doneAt || now.toISOString()) : null;
      }
    }
    store.saveState(DATA_DIR, state);
    return sendJson(res, 200, state.drill);
  }

  return sendError(res, 404, 'unknown API route');
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const pathname = url.pathname;
  if (pathname.startsWith('/api/')) {
    handleApi(req, res, pathname).catch((e) => {
      console.error(e);
      if (!res.headersSent) sendError(res, 500, e.message || 'internal error');
    });
    return;
  }
  try { serveStatic(req, res, pathname); }
  catch (e) { console.error(e); if (!res.headersSent) sendError(res, 500, 'static file error'); }
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`Heirloom ${VERSION} listening on :${PORT} (data: ${DATA_DIR})`);
});
