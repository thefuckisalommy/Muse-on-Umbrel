'use strict';
// Heirloom — plain-file JSON storage. No database server.
// All state lives in DATA_DIR/state.json; check-ins and delivery attempts
// are append-only JSONL audit logs in the same directory.
const fs = require('fs');
const path = require('path');

const STATE_FILE = 'state.json';
const CHECKIN_LOG = 'checkins.jsonl';
const DELIVERY_LOG = 'delivery.jsonl';

function defaultPart1Template() {
  return [
    'Subject: A message from {{owner_name}} — please read',
    '',
    'This is an automated message from Heirloom, sent because {{owner_name}} has not checked in for {{days_since}} days.',
    '',
    'What this is: {{owner_name}} set up a plan so that, if something happened to them, you would receive guidance on recovering their Bitcoin. This message itself contains no sensitive information.',
    '',
    'What to do next:',
    '1. Contact the executor: {{executor_contact}}',
    '2. The sealed physical packet is kept at: {{packet_location}}',
    '3. If a second, encrypted part was included with this message, open the attached decrypt page and enter the password {{owner_name}} gave you. The page works offline and the password never leaves your computer.',
    '',
    'If you believe this was sent in error — for example, {{owner_name}} is travelling or in hospital — contact them directly. There is a grace period and a pause code for exactly this situation.',
    '',
    '— Heirloom (running on {{owner_name}}\'s home server)',
  ].join('\n');
}

function defaultDrillSteps() {
  return [
    { id: 'heir-password', title: 'Confirm the heir has the password, stored somewhere safe', done: false, doneAt: null },
    { id: 'decrypt-test', title: 'Heir opens the standalone decrypt page and decrypts a test message', done: false, doneAt: null },
    { id: 'packet-walkthrough', title: 'Walk through the printed packet together while you can still fix it', done: false, doneAt: null },
    { id: 'packet-location', title: 'Verify the sealed physical packet is where Part 1 says it is', done: false, doneAt: null },
    { id: 'testnet-rehearsal', title: 'Run a test recovery on testnet (or a small real wallet)', done: false, doneAt: null },
    { id: 'channels-test', title: 'Test-send on every enabled delivery channel (STUB in this build)', done: false, doneAt: null },
  ];
}

function defaultState() {
  const now = new Date().toISOString();
  return {
    version: 1,
    createdAt: now,
    updatedAt: now,
    vault: {
      descriptor: '',
      quorumM: 2,
      quorumN: 3,
      keys: [
        { id: 'key-1', label: 'Key 1', type: 'hardware', location: '' },
        { id: 'key-2', label: 'Key 2', type: 'hardware', location: '' },
        { id: 'key-3', label: 'Key 3', type: 'hardware', location: '' },
      ],
      derivationPaths: '',
      notes: '',
      packetLocation: '',
    },
    settings: {
      checkinIntervalDays: 30,
      warn1Days: 30,
      warn2Days: 60,
      thresholdDays: 90,
      graceDays: 14,
      pauseCode: '',
      paused: false,
      vacationMode: false,
      vacationUntil: '',
    },
    checkin: { lastCheckinAt: null, totalCheckins: 0 },
    recipient: { name: '', email: '', phone: '', telegram: '' },
    channels: {
      email: { enabled: false },
      telegram: { enabled: false },
      sms: { enabled: false },
    },
    release: {
      part1Template: defaultPart1Template(),
      part2Enabled: false,
      // Ciphertext blob only, or null. The server NEVER sees the plaintext
      // or the password — encryption happens in the browser (see public/crypto.js).
      part2: null,
    },
    drill: { steps: defaultDrillSteps() },
  };
}

// Deep-merge saved state over defaults so older state files keep working
// when new fields are added.
function mergeDefaults(def, saved) {
  if (saved === null || typeof saved !== 'object' || Array.isArray(saved)) return saved === undefined ? def : saved;
  if (def === null || typeof def !== 'object' || Array.isArray(def)) return saved;
  const out = {};
  for (const k of Object.keys(def)) out[k] = mergeDefaults(def[k], saved[k]);
  for (const k of Object.keys(saved)) if (!(k in out)) out[k] = saved[k];
  return out;
}

function ensureDir(dataDir) {
  fs.mkdirSync(dataDir, { recursive: true });
}

function loadState(dataDir) {
  ensureDir(dataDir);
  const file = path.join(dataDir, STATE_FILE);
  if (!fs.existsSync(file)) {
    const state = defaultState();
    saveState(dataDir, state);
    return state;
  }
  const saved = JSON.parse(fs.readFileSync(file, 'utf8'));
  return mergeDefaults(defaultState(), saved);
}

function saveState(dataDir, state) {
  ensureDir(dataDir);
  state.updatedAt = new Date().toISOString();
  const file = path.join(dataDir, STATE_FILE);
  const tmp = file + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2) + '\n', 'utf8');
  fs.renameSync(tmp, file);
  return state;
}

function appendJsonl(dataDir, file, entry) {
  ensureDir(dataDir);
  fs.appendFileSync(path.join(dataDir, file), JSON.stringify(entry) + '\n', 'utf8');
}

function readJsonl(dataDir, file, limit) {
  const p = path.join(dataDir, file);
  if (!fs.existsSync(p)) return [];
  const lines = fs.readFileSync(p, 'utf8').split('\n').filter(Boolean);
  const entries = lines.map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
  return limit ? entries.slice(-limit) : entries;
}

function appendCheckin(dataDir, entry) { appendJsonl(dataDir, CHECKIN_LOG, entry); }
function readCheckins(dataDir, limit) { return readJsonl(dataDir, CHECKIN_LOG, limit); }
function appendDeliveryLog(dataDir, entry) { appendJsonl(dataDir, DELIVERY_LOG, entry); }
function readDeliveryLog(dataDir, limit) { return readJsonl(dataDir, DELIVERY_LOG, limit); }

module.exports = {
  loadState, saveState, defaultState,
  appendCheckin, readCheckins, appendDeliveryLog, readDeliveryLog,
};
