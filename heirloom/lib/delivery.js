'use strict';
// ============================================================================
// DELIVERY — STUB ONLY. Interface + logging, no sending.
// ============================================================================
// This module NEVER sends anything. It records what WOULD have been sent
// (channel, recipient, timestamp, subject) to the delivery log and returns
// success, so the UI and release flow can be built and tested end-to-end.
//
// Real delivery is UNDECIDED — the spec's open decision is a hosted relay
// vs on-Pi paths (Resend / Twilio / Telegram). Until that is decided, this
// stub is the whole delivery layer.
//
// HARD RULES for this module:
//   - No network calls of any kind (no fetch, no SMTP, no API clients).
//   - Never accept, store, or log API credentials, tokens, or passwords.
//   - The message body is stored only as a short preview (first 200 chars)
//     so the log stays useful without duplicating the full release text.
// ============================================================================

const { appendDeliveryLog } = require('./store');

const CHANNELS = ['email', 'telegram', 'sms'];

function isChannel(name) {
  return CHANNELS.includes(name);
}

// Record a would-be send. Returns the log entry.
function sendStub(dataDir, { channel, to, subject, body }) {
  if (!isChannel(channel)) throw new Error('unknown channel: ' + channel);
  const entry = {
    ts: new Date().toISOString(),
    stub: true,
    status: 'logged-not-sent',
    channel,
    to: to || '',
    subject: subject || '',
    bodyPreview: (body || '').slice(0, 200),
    note: 'STUB — nothing was sent. Delivery architecture undecided.',
  };
  appendDeliveryLog(dataDir, entry);
  return entry;
}

module.exports = { CHANNELS, isChannel, sendStub };
