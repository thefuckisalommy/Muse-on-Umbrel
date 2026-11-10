'use strict';
// Heirloom UI — vanilla JS, no build step. Talks to the JSON API in server.js.
// The password and Part 2 plaintext never leave this page: encryption runs
// here, in the browser, via HeirloomCrypto (public/crypto.js).
(function () {
  var S = null; // cached state

  function $(id) { return document.getElementById(id); }

  function setMsg(el, text, ok) {
    el.textContent = text;
    el.style.color = ok === false ? '#991b1b' : '';
  }

  async function api(method, path, body) {
    var res = await fetch(path, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || ('request failed: ' + res.status));
    return data;
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString();
  }

  function fmtDays(n) {
    if (n === null || n === undefined) return '—';
    return n + (n === 1 ? ' day' : ' days');
  }

  // ---------- tabs ----------
  document.getElementById('tabs').addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-tab]');
    if (!btn) return;
    document.querySelectorAll('#tabs button').forEach(function (b) { b.classList.toggle('active', b === btn); });
    document.querySelectorAll('section.tab').forEach(function (s) { s.classList.toggle('active', s.id === 'tab-' + btn.dataset.tab); });
  });

  // ---------- load & render ----------
  async function load() {
    S = await api('GET', '/api/state');
    var status = await api('GET', '/api/status');
    var checkins = await api('GET', '/api/checkins');
    var dlog = await api('GET', '/api/delivery/log');
    renderDashboard(status, checkins);
    renderVault();
    renderRelease();
    renderDelivery(dlog);
    renderDrill();
    renderSettings();
  }

  var PILL_CLASS = {
    'on-track': 'ok', 'warning-1': 'warn', 'warning-2': 'warn',
    'grace': 'bad', 'release-due': 'bad',
    'paused': 'muted', 'vacation': 'muted', 'not-started': 'muted',
  };

  function renderDashboard(status, checkins) {
    var pill = $('status-pill');
    pill.textContent = status.label;
    pill.className = 'pill ' + (PILL_CLASS[status.state] || 'muted');
    $('header-pill').textContent = status.label;
    $('header-pill').className = 'pill ' + (PILL_CLASS[status.state] || 'muted');
    $('status-line').textContent =
      status.state === 'not-started' ? 'No check-in recorded yet. Tap “Check in” to start the clock.'
      : status.state === 'vacation' ? 'Vacation mode is on' + (status.vacationUntil ? ' until ' + status.vacationUntil : '') + '. Releases are held.'
      : status.state === 'paused' ? 'Releases are paused. Check in to resume automatically.'
      : 'Last check-in ' + fmtDays(status.daysSince) + ' ago.';
    $('last-checkin').textContent = fmtDate(status.lastCheckinAt);
    $('days-since').textContent = fmtDays(status.daysSince);
    $('next-due').textContent = status.nextDue ? fmtDate(status.nextDue) : '—';
    $('total-checkins').textContent = String(status.totalCheckins);
    $('readiness-part2').textContent = status.part2Enabled
      ? (status.part2Stored ? 'Enabled, ciphertext stored' : 'Enabled, no ciphertext stored yet')
      : 'Disabled (release is Part 1 only)';
    var enabled = ['email', 'telegram', 'sms'].filter(function (c) { return S.channels[c].enabled; });
    $('readiness-channels').textContent = enabled.length
      ? enabled.join(', ') + ' (STUB — nothing is sent)'
      : 'None enabled';
    var done = S.drill.steps.filter(function (s) { return s.done; }).length;
    $('readiness-drill').textContent = done + ' of ' + S.drill.steps.length + ' steps done';
    var tbody = $('recent-checkins');
    tbody.innerHTML = '';
    if (!checkins.length) {
      tbody.innerHTML = '<tr><td colspan="3" class="muted">None yet.</td></tr>';
    } else {
      checkins.slice(0, 10).forEach(function (c) {
        var tr = document.createElement('tr');
        tr.innerHTML = '<td></td><td></td><td></td>';
        tr.children[0].textContent = fmtDate(c.ts);
        tr.children[1].textContent = c.method || 'app';
        tr.children[2].textContent = c.autoResumed ? 'auto-resumed from pause' : '';
        tbody.appendChild(tr);
      });
    }
  }

  $('btn-checkin').addEventListener('click', async function () {
    try {
      var r = await api('POST', '/api/checkin', { method: 'app' });
      setMsg($('checkin-msg'), 'Checked in just now.' + (r.autoResumed ? ' Pause cleared automatically.' : ''), true);
      await load();
    } catch (e) { setMsg($('checkin-msg'), e.message, false); }
  });

  // ---------- vault ----------
  function renderVault() {
    $('vault-descriptor').value = S.vault.descriptor || '';
    $('vault-m').value = S.vault.quorumM;
    $('vault-n').value = S.vault.quorumN;
    $('vault-paths').value = S.vault.derivationPaths || '';
    $('vault-notes').value = S.vault.notes || '';
    $('vault-packet-location').value = S.vault.packetLocation || '';
    renderKeys();
    renderPrintPacket();
  }

  function renderKeys() {
    var tbody = $('keys-body');
    tbody.innerHTML = '';
    S.vault.keys.forEach(function (k, i) {
      var tr = document.createElement('tr');
      var tdLabel = document.createElement('td');
      var inLabel = document.createElement('input');
      inLabel.type = 'text'; inLabel.value = k.label; inLabel.dataset.i = i; inLabel.dataset.f = 'label';
      tdLabel.appendChild(inLabel);
      var tdType = document.createElement('td');
      var sel = document.createElement('select');
      ['hardware', 'software', 'paper', 'other'].forEach(function (t) {
        var o = document.createElement('option'); o.value = t; o.textContent = t;
        if (k.type === t) o.selected = true;
        sel.appendChild(o);
      });
      sel.dataset.i = i; sel.dataset.f = 'type';
      tdType.appendChild(sel);
      var tdLoc = document.createElement('td');
      var inLoc = document.createElement('input');
      inLoc.type = 'text'; inLoc.value = k.location || ''; inLoc.placeholder = 'e.g. Home safe';
      inLoc.dataset.i = i; inLoc.dataset.f = 'location';
      tdLoc.appendChild(inLoc);
      var tdRm = document.createElement('td');
      var rm = document.createElement('button'); rm.textContent = '✕'; rm.title = 'Remove key';
      rm.addEventListener('click', function () { S.vault.keys.splice(i, 1); renderKeys(); });
      tdRm.appendChild(rm);
      tr.append(tdLabel, tdType, tdLoc, tdRm);
      tbody.appendChild(tr);
    });
  }

  function collectKeys() {
    var keys = [];
    document.querySelectorAll('#keys-body tr').forEach(function (tr, i) {
      var label = tr.querySelector('[data-f="label"]').value;
      var type = tr.querySelector('[data-f="type"]').value;
      var location = tr.querySelector('[data-f="location"]').value;
      keys.push({ id: S.vault.keys[i] ? S.vault.keys[i].id : 'key-' + (i + 1), label: label, type: type, location: location });
    });
    return keys;
  }

  $('btn-add-key').addEventListener('click', function () {
    S.vault.keys = collectKeys();
    S.vault.keys.push({ id: 'key-' + Date.now(), label: 'Key ' + (S.vault.keys.length + 1), type: 'hardware', location: '' });
    renderKeys();
  });

  $('btn-save-vault').addEventListener('click', async function () {
    try {
      var v = await api('PUT', '/api/vault', {
        descriptor: $('vault-descriptor').value,
        quorumM: parseInt($('vault-m').value, 10),
        quorumN: parseInt($('vault-n').value, 10),
        keys: collectKeys(),
        derivationPaths: $('vault-paths').value,
        notes: $('vault-notes').value,
        packetLocation: $('vault-packet-location').value,
      });
      S.vault = v;
      setMsg($('vault-msg'), 'Vault map saved.', true);
      renderPrintPacket();
    } catch (e) { setMsg($('vault-msg'), e.message, false); }
  });

  function renderPrintPacket() {
    $('pp-generated').textContent = new Date().toLocaleString();
    $('pp-quorum').textContent = S.vault.quorumM + ' of ' + S.vault.quorumN + ' signatures required';
    var tbody = $('pp-keys');
    tbody.innerHTML = '';
    S.vault.keys.forEach(function (k) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td></td><td></td><td></td>';
      tr.children[0].textContent = k.label;
      tr.children[1].textContent = k.type;
      tr.children[2].textContent = k.location || '—';
      tbody.appendChild(tr);
    });
    $('pp-descriptor').textContent = S.vault.descriptor || '—';
    $('pp-paths').textContent = S.vault.derivationPaths || '—';
    $('pp-location').textContent = S.vault.packetLocation || '—';
    $('pp-notes').textContent = S.vault.notes || '—';
  }

  $('btn-print-packet').addEventListener('click', function () {
    renderPrintPacket();
    window.print();
  });

  // ---------- release ----------
  function renderRelease() {
    $('part1-template').value = S.release.part1Template;
    $('part1-preview').style.display = 'none';
    $('part2-enabled').checked = !!S.release.part2Enabled;
    $('part2-status').textContent = S.release.part2
      ? 'Ciphertext stored (created ' + fmtDate(S.release.part2.createdAt) + ', KDF: ' + S.release.part2.kdf + '). The server holds ciphertext only.'
      : 'No ciphertext stored.';
  }

  $('btn-save-part1').addEventListener('click', async function () {
    try {
      await api('PUT', '/api/release/part1', { template: $('part1-template').value });
      setMsg($('part1-msg'), 'Part 1 template saved.', true);
    } catch (e) { setMsg($('part1-msg'), e.message, false); }
  });

  $('btn-preview-part1').addEventListener('click', async function () {
    try {
      var r = await api('GET', '/api/release/preview');
      var pre = $('part1-preview');
      pre.textContent = r.rendered;
      pre.style.display = 'block';
    } catch (e) { setMsg($('part1-msg'), e.message, false); }
  });

  $('btn-encrypt').addEventListener('click', async function () {
    var plaintext = $('part2-plaintext').value;
    var password = $('part2-password').value;
    if (!plaintext) { setMsg($('part2-status'), 'Write the recovery guide first.', false); return; }
    if (!password) { setMsg($('part2-status'), 'Enter the password (the one the heir already holds).', false); return; }
    try {
      setMsg($('part2-status'), 'Encrypting in your browser…');
      var blob = await window.HeirloomCrypto.encrypt(plaintext, password);
      await api('PUT', '/api/release/part2', { enabled: true, ciphertext: blob });
      $('part2-enabled').checked = true;
      $('part2-plaintext').value = '';
      $('part2-password').value = '';
      S.release.part2Enabled = true;
      S.release.part2 = blob;
      setMsg($('part2-status'), 'Encrypted and stored. Ciphertext only — the server never saw the plaintext or the password.', true);
      await load();
    } catch (e) { setMsg($('part2-status'), e.message, false); }
  });

  $('btn-clear-part2').addEventListener('click', async function () {
    try {
      await api('PUT', '/api/release/part2', { enabled: false, ciphertext: null });
      S.release.part2Enabled = false;
      S.release.part2 = null;
      renderRelease();
      setMsg($('part2-status'), 'Stored ciphertext removed. Part 2 is now disabled.', true);
    } catch (e) { setMsg($('part2-status'), e.message, false); }
  });

  $('part2-enabled').addEventListener('change', async function () {
    try {
      await api('PUT', '/api/release/part2', { enabled: $('part2-enabled').checked, ciphertext: S.release.part2 || null });
      await load();
    } catch (e) { setMsg($('part2-status'), e.message, false); }
  });

  $('btn-load-stored-blob').addEventListener('click', function () {
    if (!S.release.part2) { setMsg($('part2-status'), 'No ciphertext stored yet.', false); return; }
    $('decrypt-blob').value = JSON.stringify(S.release.part2, null, 2);
  });

  $('btn-decrypt').addEventListener('click', async function () {
    var out = $('decrypt-output');
    try {
      var blob = JSON.parse($('decrypt-blob').value);
      var pt = await window.HeirloomCrypto.decrypt(blob, $('decrypt-password').value);
      out.textContent = pt;
      out.style.display = 'block';
    } catch (e) {
      out.textContent = 'Error: ' + e.message;
      out.style.display = 'block';
    }
  });

  // ---------- delivery ----------
  var CHANNEL_META = {
    email: { title: 'Email', hint: 'Sent via an email API (e.g. Resend) once delivery is built. Uses the recipient email above.' },
    telegram: { title: 'Telegram', hint: 'Sent by a Telegram bot DM. The heir must have started a chat with the bot in advance.' },
    sms: { title: 'SMS', hint: 'Sent via an SMS API (e.g. Twilio) once delivery is built. Uses the recipient phone above.' },
  };

  function renderDelivery(dlog) {
    var list = $('channel-list');
    list.innerHTML = '';
    Object.keys(CHANNEL_META).forEach(function (ch) {
      var meta = CHANNEL_META[ch];
      var card = document.createElement('div');
      card.className = 'card';
      card.style.marginBottom = '12px';
      var label = document.createElement('label');
      label.style.margin = '0';
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = !!S.channels[ch].enabled;
      cb.addEventListener('change', async function () {
        try {
          var patch = {};
          patch[ch] = { enabled: cb.checked };
          var updated = await api('PUT', '/api/channels', patch);
          S.channels = updated;
          setMsg($('delivery-msg'), meta.title + (cb.checked ? ' enabled.' : ' disabled.'), true);
          await load();
        } catch (e) { setMsg($('delivery-msg'), e.message, false); cb.checked = !cb.checked; }
      });
      label.appendChild(cb);
      label.appendChild(document.createTextNode(' ' + meta.title + ' '));
      var stub = document.createElement('span');
      stub.className = 'stub'; stub.textContent = 'STUB';
      label.appendChild(stub);
      var hint = document.createElement('p');
      hint.className = 'muted'; hint.textContent = meta.hint;
      var testBtn = document.createElement('button');
      testBtn.textContent = 'Test send (STUB)';
      testBtn.addEventListener('click', async function () {
        try {
          var r = await api('POST', '/api/delivery/test', { channel: ch });
          setMsg($('delivery-msg'), 'STUB logged: a ' + ch + ' message to ' + r.entry.to + ' would have been sent at ' + r.entry.ts + '. Nothing was actually sent.', true);
          await load();
        } catch (e) { setMsg($('delivery-msg'), e.message, false); }
      });
      card.append(label, hint, testBtn);
      list.appendChild(card);
    });
    var tbody = $('delivery-log');
    tbody.innerHTML = '';
    if (!dlog.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="muted">Nothing logged yet.</td></tr>';
    } else {
      dlog.forEach(function (e) {
        var tr = document.createElement('tr');
        tr.innerHTML = '<td></td><td></td><td></td><td></td><td></td>';
        tr.children[0].textContent = fmtDate(e.ts);
        tr.children[1].textContent = e.channel;
        tr.children[2].textContent = e.to || '—';
        tr.children[3].textContent = e.subject || '—';
        tr.children[4].textContent = e.status || '';
        tbody.appendChild(tr);
      });
    }
  }

  $('btn-save-recipient').addEventListener('click', async function () {
    try {
      var r = await api('PUT', '/api/recipient', {
        name: $('rcp-name').value,
        email: $('rcp-email').value,
        phone: $('rcp-phone').value,
        telegram: $('rcp-telegram').value,
      });
      S.recipient = r;
      setMsg($('recipient-msg'), 'Recipient saved.', true);
    } catch (e) { setMsg($('recipient-msg'), e.message, false); }
  });

  // ---------- fire drill ----------
  function renderDrill() {
    var list = $('drill-list');
    list.innerHTML = '';
    S.drill.steps.forEach(function (step) {
      var label = document.createElement('label');
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = !!step.done;
      cb.addEventListener('change', async function () {
        try {
          var steps = S.drill.steps.map(function (s) {
            return { id: s.id, done: s.id === step.id ? cb.checked : s.done };
          });
          var d = await api('PUT', '/api/drill', { steps: steps });
          S.drill = d;
          renderDrill();
        } catch (e) { setMsg($('drill-msg'), e.message, false); cb.checked = !cb.checked; }
      });
      var span = document.createElement('span');
      span.textContent = step.title;
      if (step.done) span.className = 'done';
      label.append(cb, span);
      list.appendChild(label);
    });
    var done = S.drill.steps.filter(function (s) { return s.done; }).length;
    var total = S.drill.steps.length;
    $('drill-progress').textContent = done + ' of ' + total + ' steps done';
    $('drill-progress-bar').style.width = (total ? Math.round(100 * done / total) : 0) + '%';
  }

  $('btn-reset-drill').addEventListener('click', async function () {
    try {
      var steps = S.drill.steps.map(function (s) { return { id: s.id, done: false }; });
      var d = await api('PUT', '/api/drill', { steps: steps });
      S.drill = d;
      renderDrill();
      setMsg($('drill-msg'), 'Checklist reset.', true);
    } catch (e) { setMsg($('drill-msg'), e.message, false); }
  });

  // ---------- settings ----------
  function renderSettings() {
    $('set-interval').value = S.settings.checkinIntervalDays;
    $('set-warn1').value = S.settings.warn1Days;
    $('set-warn2').value = S.settings.warn2Days;
    $('set-threshold').value = S.settings.thresholdDays;
    $('set-grace').value = S.settings.graceDays;
    $('set-pause-code').value = S.settings.pauseCode || '';
    $('set-vacation').checked = !!S.settings.vacationMode;
    $('set-vacation-until').value = S.settings.vacationUntil || '';
    // Recipient fields live in Delivery; fill them here too for convenience.
    $('rcp-name').value = S.recipient.name || '';
    $('rcp-email').value = S.recipient.email || '';
    $('rcp-phone').value = S.recipient.phone || '';
    $('rcp-telegram').value = S.recipient.telegram || '';
  }

  $('btn-save-settings').addEventListener('click', async function () {
    try {
      var s = await api('PUT', '/api/settings', {
        checkinIntervalDays: parseInt($('set-interval').value, 10),
        warn1Days: parseInt($('set-warn1').value, 10),
        warn2Days: parseInt($('set-warn2').value, 10),
        thresholdDays: parseInt($('set-threshold').value, 10),
        graceDays: parseInt($('set-grace').value, 10),
        pauseCode: $('set-pause-code').value,
        vacationMode: $('set-vacation').checked,
        vacationUntil: $('set-vacation-until').value,
      });
      S.settings = s;
      setMsg($('settings-msg'), 'Settings saved.', true);
      await load();
    } catch (e) { setMsg($('settings-msg'), e.message, false); }
  });

  $('btn-pause').addEventListener('click', async function () {
    try {
      await api('POST', '/api/pause', { code: $('pause-code-input').value });
      setMsg($('pause-msg'), 'Releases paused.', true);
      await load();
    } catch (e) { setMsg($('pause-msg'), e.message, false); }
  });

  $('btn-resume').addEventListener('click', async function () {
    try {
      await api('POST', '/api/resume', {});
      setMsg($('pause-msg'), 'Resumed.', true);
      await load();
    } catch (e) { setMsg($('pause-msg'), e.message, false); }
  });

  $('btn-export').addEventListener('click', async function () {
    try {
      var state = await api('GET', '/api/state');
      var blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'heirloom-state.json';
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { setMsg($('settings-msg'), e.message, false); }
  });

  load().catch(function (e) {
    document.querySelector('main').insertAdjacentHTML('afterbegin',
      '<div class="notice bad">Could not load Heirloom state: ' + e.message + '</div>');
  });
})();
