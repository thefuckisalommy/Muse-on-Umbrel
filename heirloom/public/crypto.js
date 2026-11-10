// ============================================================================
// Heirloom crypto helpers — WebCrypto AES-GCM + PBKDF2.
// ----------------------------------------------------------------------------
// INTERIM IMPLEMENTATION. The spec calls for Argon2id key derivation. PBKDF2
// (250,000 iterations, SHA-256) is used here so the encrypt/decrypt
// round-trip works with zero dependencies and no build step. Before real use,
// swap the KDF for argon2-browser (WASM) — the blob format carries a `kdf`
// field precisely so an Argon2id blob can be told apart from these PBKDF2
// blobs. The cipher (AES-GCM-256) stays the same.
//
// Security properties (unchanged by the swap):
//   - The password never leaves the browser. The server only ever receives
//     the ciphertext blob ({v, kdf, iter, salt, iv, ct}).
//   - Salt (16 bytes) and IV (12 bytes) are fresh random values per message.
//   - Decryption is authenticated: a wrong password fails, it does not
//     silently produce garbage.
// ============================================================================
(function (global) {
  'use strict';

  var subtle = global.crypto && global.crypto.subtle;
  var getRandomValues = global.crypto && global.crypto.getRandomValues
    ? function (arr) { return global.crypto.getRandomValues(arr); }
    : function () { throw new Error('WebCrypto not available'); };

  var te = new TextEncoder();
  var td = new TextDecoder();

  function b64encode(buf) {
    var bytes = new Uint8Array(buf);
    if (typeof Buffer !== 'undefined') return Buffer.from(bytes).toString('base64');
    var s = '';
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s);
  }

  function b64decode(s) {
    if (typeof Buffer !== 'undefined') return Buffer.from(s, 'base64');
    var bin = atob(s);
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  var ITERATIONS = 250000;

  async function deriveKey(password, salt) {
    if (!subtle) throw new Error('WebCrypto not available in this context');
    var base = await subtle.importKey('raw', te.encode(password), 'PBKDF2', false, ['deriveKey']);
    return subtle.deriveKey(
      { name: 'PBKDF2', salt: salt, iterations: ITERATIONS, hash: 'SHA-256' },
      base,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt']
    );
  }

  async function encrypt(plaintext, password) {
    if (!password) throw new Error('password is required');
    var salt = getRandomValues(new Uint8Array(16));
    var iv = getRandomValues(new Uint8Array(12));
    var key = await deriveKey(password, salt);
    var ct = await subtle.encrypt({ name: 'AES-GCM', iv: iv }, key, te.encode(plaintext));
    return {
      v: 1,
      kdf: 'pbkdf2-sha256', // INTERIM — see header. Real use: 'argon2id'.
      iter: ITERATIONS,
      salt: b64encode(salt),
      iv: b64encode(iv),
      ct: b64encode(ct),
    };
  }

  async function decrypt(blob, password) {
    if (!blob || !blob.salt || !blob.iv || !blob.ct) throw new Error('not a valid ciphertext blob');
    if (blob.kdf && blob.kdf !== 'pbkdf2-sha256') {
      throw new Error('unsupported KDF: ' + blob.kdf + ' (this build only handles pbkdf2-sha256)');
    }
    if (!password) throw new Error('password is required');
    var salt = b64decode(blob.salt);
    var iv = b64decode(blob.iv);
    var key = await deriveKey(password, salt);
    var pt;
    try {
      pt = await subtle.decrypt({ name: 'AES-GCM', iv: iv }, key, b64decode(blob.ct));
    } catch (e) {
      throw new Error('decryption failed — wrong password, or the blob was damaged');
    }
    return td.decode(pt);
  }

  var api = { encrypt: encrypt, decrypt: decrypt, ITERATIONS: ITERATIONS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  global.HeirloomCrypto = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
