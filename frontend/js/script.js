const API_BASE = localStorage.getItem('traceloop_api_base') || 'https://trace-loop.onrender.com';
const TOKEN_KEY   = 'traceloop_access_token';
const REFRESH_KEY = 'traceloop_refresh_token';
const USER_KEY    = 'traceloop_user';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const token = () => localStorage.getItem(TOKEN_KEY);
const user  = () => { try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; } };

const esc = v => String(v ?? '').replace(/[&<>'"]/g, c =>
  ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#039;', '"':'&quot;' }[c]));

const fmtDate = v => v ? new Date(v).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—';

const money = v => v == null
  ? 'Price on request'
  : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v);

const statusClass = v => String(v || '').toLowerCase().replace(/[^a-z]/g, '-');

/* ── Toast ───────────────────────────────────────────────────── */
function toast(message, type = 'success') {
  let n = $('#toast');
  if (!n) { n = document.createElement('div'); n.id = 'toast'; document.body.appendChild(n); }
  n.className = `toast ${type}`;
  n.textContent = message;
  requestAnimationFrame(() => n.classList.add('show'));
  clearTimeout(window.__toast);
  window.__toast = setTimeout(() => n.classList.remove('show'), 3600);
}

/* ── Button busy state ───────────────────────────────────────── */
function setBusy(b, busy, label = 'Working…') {
  if (!b) return;
  if (busy) { b.dataset.label = b.textContent; b.disabled = true; b.textContent = label; }
  else { b.disabled = false; b.textContent = b.dataset.label || b.textContent; }
}

/* ── API helper ──────────────────────────────────────────────── */
async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (token() && options.auth !== false) headers.Authorization = `Bearer ${token()}`;

  let r = await fetch(`${API_BASE}${path}`, { ...options, headers });

  // Auto-refresh on 401
  if (r.status === 401 && localStorage.getItem(REFRESH_KEY)) {
    const rr = await fetch(`${API_BASE}/api/v1/token/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: localStorage.getItem(REFRESH_KEY) })
    });
    if (rr.ok) {
      const fresh = envelope(await rr.json());
      localStorage.setItem(TOKEN_KEY, fresh.access_token);
      if (fresh.refresh_token) localStorage.setItem(REFRESH_KEY, fresh.refresh_token);
      headers.Authorization = `Bearer ${fresh.access_token}`;
      r = await fetch(`${API_BASE}${path}`, { ...options, headers });
    }
  }

  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401) logout(false);
    throw new Error(errorMsg(body));
  }
  return body;
}

function envelope(p) { return p?.data !== undefined ? p.data : p; }
// Replace this function
function errorMsg(p) { 
  const msg = p?.detail || p?.error?.message || p?.error || p?.message || 'Something went wrong.';
  
  // THE FIX: If FastAPI returns an Array of errors (like a 422), make it readable!
  if (Array.isArray(msg)) return msg.map(m => m.msg || JSON.stringify(m)).join(' | ');
  if (typeof msg === 'object') return JSON.stringify(msg);
  
  return msg;
}

/* ── Session ─────────────────────────────────────────────────── */
function saveSession(d) {
  localStorage.setItem(TOKEN_KEY, d.access_token);
  if (d.refresh_token) localStorage.setItem(REFRESH_KEY, d.refresh_token);
}

function logout(redir = true) {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
  if (redir) location.href = 'register.html';
}

function requireAuth(allowed = []) {
  if (!token()) { location.href = 'login.html'; return false; }
  if (allowed.length && !allowed.includes(user()?.role)) {
    toast('This workspace is not available for your role.', 'error');
    setTimeout(() => location.href = 'dashboard.html', 700);
    return false;
  }
  return true;
}

/* ── Chip / Empty helpers ────────────────────────────────────── */
function chip(v) {
  const label = String(v || 'UNKNOWN').replace(/_/g, ' ');
  return `<span class="chip chip-${statusClass(v)}">${esc(label)}</span>`;
}

function empty(title, sub) {
  return `<div class="empty">
    <div class="empty-icon">⌁</div>
    <strong>${esc(title)}</strong>
    <p>${esc(sub)}</p>
  </div>`;
}

/* ── SHA-256 file hash ───────────────────────────────────────── */
async function hashFile(file) {
  const bytes = await file.arrayBuffer();
  const hash  = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(hash)].map(b => b.toString(16).padStart(2, '0')).join('');
}

/* ═══════════════════════════════════════════════════════════════
   NAV SHELL
══════════════════════════════════════════════════════════════ */
function renderShell() {
  const nav = $('.site-nav');
  if (!nav) return;

  const current = document.body.dataset.page;
  const logged  = !!token();
  const me      = user();

  const links = [
    ['dashboard.html', 'Workspace', 'dashboard'],
    ['track.html',     'Track',     'track'],
    ['transfer.html',  'Marketplace','transfer'],
    ['network.html',   'Network',    'network'],
  ];
  if (!logged) links.push(['register.html', 'Get started', 'register']);

  nav.innerHTML = `
    <a class="brand" href="index.html">
      <span class="brand-mark">TL</span>
      <span>TRACE<span class="accent">-LOOP</span></span>
    </a>

    <button class="nav-toggle" aria-label="Menu">☰</button>

    <div class="nav-links">
      ${links.map(([href, label, key]) =>
        `<a class="${current === key ? 'active' : ''}" href="${href}">${label}</a>`
      ).join('')}

      ${logged
        ? `<button class="nav-logout" data-logout>Sign out</button>`
        : `<a href="login.html" class="${current === 'login' ? 'active' : ''}">Sign in</a>`
      }
    </div>

    ${logged
      ? `<div class="nav-user">
           <span class="avatar">${esc((me?.name || 'T')[0].toUpperCase())}</span>
           <span>${esc(me?.name || 'Member')}<small>${esc(me?.role || '')}</small></span>
         </div>`
      : `<a class="nav-cta" href="register.html">Get started</a>`
    }
  `;

  $('.nav-toggle', nav)?.addEventListener('click', () => nav.classList.toggle('open'));
  $('[data-logout]', nav)?.addEventListener('click', () => logout());
}

/* ═══════════════════════════════════════════════════════════════
   LANDING
══════════════════════════════════════════════════════════════ */
function initLanding() {
  $('#health-check')?.addEventListener('click', async e => {
    setBusy(e.currentTarget, true, 'Checking…');
    try {
      const d = envelope(await api('/health', { auth: false }));
      toast(`API ${d.api || 'online'} · Database ${d.database || 'ok'}`);
    } catch (x) {
      toast(x.message, 'error');
    } finally {
      setBusy(e.currentTarget, false);
    }
  });
}

/* ═══════════════════════════════════════════════════════════════
   LOGIN
══════════════════════════════════════════════════════════════ */
async function initLogin() {
  if (token()) { location.href = 'dashboard.html'; return; }

  const send   = $('#login-send-otp');
  const verify = $('#login-verify-otp');

  // THE FIX: Prevent non-numbers and Auto-click Verify on the 6th digit
  $('#login-otp-code')?.addEventListener('input', e => {
    e.target.value = e.target.value.replace(/[^0-9]/g, '');
    if (e.target.value.length === 6) verify.click();
  });

  send?.addEventListener('click', async () => {
    const phone = $('#login-phone').value.trim();
    if (!phone) { toast('Enter your phone number.', 'error'); return; }

    setBusy(send, true, 'Sending…');
    try {
      const d = envelope(await api('/api/v1/otp/send', {
        method: 'POST', body: JSON.stringify({ phone }), auth: false
      }));
      $('#login-otp-phone').textContent = phone;
      $('#login-otp-panel').hidden = false;
      
      // THE FIX: The Magic Trick
      $('#login-otp-preview').textContent = 'OTP sent to your phone.';
      if (d.otp_preview) console.log("%c🔑 DEV OTP: " + d.otp_preview, "color: #25D366; font-size: 16px; font-weight: bold;");
      
      $('#login-otp-code').focus();
      toast('OTP sent.');
    } catch (x) { toast(x.message, 'error'); }
    finally { setBusy(send, false); }
  });

  verify?.addEventListener('click', async () => {
    const phone = $('#login-phone').value.trim();
    const otp   = $('#login-otp-code').value.trim();
    if (!phone || !otp) { toast('Enter the OTP.', 'error'); return; }

    setBusy(verify, true, 'Signing in…');
    try {
      const d = envelope(await api('/api/v1/otp/verify', {
        method: 'POST', body: JSON.stringify({ phone, otp_code: otp }), auth: false
      }));

      // if (d.status && d.status !== 'ACTIVE') {
      //   $('#login-status').textContent = `Account is ${d.status}. Complete KYC or await admin approval.`;
      //   toast('Account not active yet.', 'error');
      //   return;
      // }

      saveSession(d);
      const me = envelope(await api('/api/v1/me'));
      localStorage.setItem(USER_KEY, JSON.stringify(me));
      toast('Signed in.');
      location.href = 'dashboard.html';
    } catch (x) {
      toast(x.message, 'error');
      $('#login-status').textContent = x.message;
    } finally { setBusy(verify, false); }
  });
}

/* ═══════════════════════════════════════════════════════════════
   REGISTER  (fixed — matches backend exactly)
══════════════════════════════════════════════════════════════ */

// Maps role → KYC document metadata
const KYC_META = {
  FIRST_BUYER: {
    doc_type:   'PURCHASE_PROOF',
    label:      'Purchase proof',
    needSerial: true,
    notice: `
      <div style="background:var(--amber-bg);border:1px solid var(--amber);border-radius:var(--radius);padding:14px 16px;font-size:.875rem;color:#92400e;margin-bottom:20px">
        <strong style="display:block;margin-bottom:8px">📄 What to upload</strong>
        GST invoice, warranty registration email, or authorized dealer receipt for the laptop.
        <ul style="margin:8px 0 0 16px">
          <li>The document must clearly show the laptop's <strong>serial number</strong>.</li>
          <li>Enter that exact serial number in the field below.</li>
          <li>Admin verifies your identity <em>and</em> the serial together — both must match.</li>
          <li>Device registration is only unlocked after admin approves this document.</li>
        </ul>
      </div>`,
    activation: 'Admin reviews your purchase proof + serial. Activated on approval.',
  },
  BUYER: {
    doc_type:   'BUSINESS_PROOF',
    label:      'Identity proof',
    needSerial: false,
    notice: `<div class="notice info" style="margin-bottom:20px">Upload any government-issued ID — Aadhaar, PAN card, passport, or voter ID.</div>`,
    activation: 'Account activated by admin after document review.',
  },
  RESELLER: {
    doc_type:   'BUSINESS_PROOF',
    label:      'Business proof',
    needSerial: true,
    notice: `
      <div style="background:var(--amber-bg);border:1px solid var(--amber);border-radius:var(--radius);padding:14px 16px;font-size:.875rem;color:#92400e;margin-bottom:20px">
        <strong style="display:block;margin-bottom:8px">📄 What to upload</strong>
        Trade license or shop establishment certificate.
        <ul style="margin:8px 0 0 16px">
          <li>Your GST number must match what you entered during registration.</li>
          <li>Provide a device serial number if you're registering an initial device.</li>
        </ul>
      </div>`,
    activation: 'Account activated by admin after GST + business proof verification.',
  },
  VERIFIABLE: {
    doc_type:   'BRAND_AUTH',
    label:      'Brand authorization letter',
    needSerial: false,
    notice: `<div class="notice info" style="margin-bottom:20px">Upload the manufacturer authorization letter from Dell, HP, Lenovo, Asus, etc. Must be current and signed by the brand.</div>`,
    activation: '⚠ Manual admin activation — in-person onboarding by the Trace-Loop team.',
  },
  RECYCLER: {
    doc_type:   'CPCB_CERT',
    label:      'CPCB registration certificate',
    needSerial: false,
    notice: `<div class="notice info" style="margin-bottom:20px">Upload your CPCB e-waste recycler registration certificate. The CPCB number must match what you entered during registration.</div>`,
    activation: '⚠ Manual admin activation. CPCB certificate is mandatory.',
  },
};

const ROLE_ACTIVATION = {
  FIRST_BUYER: 'Admin reviews your purchase proof and activates your account.',
  BUYER:       'Admin reviews your ID and activates your account.',
  RESELLER:    'Admin verifies your GST + business proof. Manual activation.',
  VERIFIABLE:  '⚠ Manual admin activation only. In-person onboarding required.',
  RECYCLER:    '⚠ Manual admin activation only. CPCB number is verified.',
};

function initRegister() {
  let selectedRole = '';

  // ── Role card selection ──────────────────────────────────────
  $$('.role-card').forEach(c => c.addEventListener('click', () => {
    $$('.role-card').forEach(x => x.classList.remove('selected'));
    c.classList.add('selected');
    selectedRole = c.dataset.role;
    $('#role-field').value = selectedRole;

    // Show/hide role-specific form fields
    $$('.role-required').forEach(el => el.style.display = 'none');
    if (selectedRole === 'RESELLER')   $$('.reseller-field').forEach(el => el.style.display = 'block');
    if (selectedRole === 'RECYCLER')   $$('.recycler-field').forEach(el => el.style.display = 'block');
    if (selectedRole === 'VERIFIABLE') $$('.verifiable-field').forEach(el => el.style.display = 'block');

    // Role notice
    const notice = $('#role-notice');
    if (notice) {
      notice.innerHTML = `<div class="notice info">
        <strong>${esc(selectedRole.replace('_', ' '))}</strong> — ${esc(ROLE_ACTIVATION[selectedRole] || '')}
      </div>`;
      notice.style.display = 'block';
    }

    $('#btn-create').disabled = false;
  }));

  // ── Step 1 → submit registration ────────────────────────────
  $('#register-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    if (!selectedRole) { toast('Please choose a role first.', 'error'); return; }

    const fd = new FormData(e.target);
    const payload = { role: selectedRole };
    for (const [k, v] of fd.entries()) { if (v.trim()) payload[k] = v.trim(); }
    // Remove empty optional fields — backend validator rejects nulls for role-gated fields
    ['gst_number', 'cpcb_number', 'brand_auth_code', 'service_center_id']
      .forEach(k => { if (!payload[k]) delete payload[k]; });

    setBusy(e.submitter, true, 'Creating account…');
    try {
      await api('/api/v1/register', { method: 'POST', body: JSON.stringify(payload), auth: false });
      goStep(2);
      $('#otp-phone-display').textContent = payload.phone;
      toast('Account created. Verify your phone to continue.');
    } catch (x) { toast(x.message, 'error'); }
    finally { setBusy(e.submitter, false); }
  });
  // THE FIX: Auto-click Verify on the 6th digit for Registration too
  $('#otp-input')?.addEventListener('input', e => {
    e.target.value = e.target.value.replace(/[^0-9]/g, '');
    if (e.target.value.length === 6) $('#btn-verify-otp')?.click();
  });

  // ── Step 2 → send OTP ───────────────────────────────────────
  $('#btn-send-otp')?.addEventListener('click', async e => {
    setBusy(e.currentTarget, true, 'Sending…');
    try {
      const phone = $('#reg-phone').value.trim();
      const d = envelope(await api('/api/v1/otp/send', {
        method: 'POST', body: JSON.stringify({ phone }), auth: false
      }));
      const box = $('#otp-preview-box');
      box.style.display = 'block';
      
      // THE FIX: The Magic Trick
      box.textContent = 'OTP sent to your phone.';
      if (d.otp_preview) console.log("%c🔑 DEV OTP: " + d.otp_preview, "color: #25D366; font-size: 16px; font-weight: bold;");
      
      $('#otp-input').focus();
      $('#btn-verify-otp').disabled = false;
      toast('OTP sent.');
    } catch (x) { toast(x.message, 'error'); }
    finally { setBusy(e.currentTarget, false); }
  });

  // ── Step 2 → verify OTP ─────────────────────────────────────
  $('#btn-verify-otp')?.addEventListener('click', async e => {
    const phone = $('#reg-phone').value.trim();
    const otp   = $('#otp-input').value.trim();
    if (!otp) { toast('Enter the OTP first.', 'error'); return; }

    setBusy(e.currentTarget, true, 'Verifying…');
    try {
      const d = envelope(await api('/api/v1/otp/verify', {
        method: 'POST', body: JSON.stringify({ phone, otp_code: otp }), auth: false
      }));
      saveSession(d);
      const me = envelope(await api('/api/v1/me'));
      localStorage.setItem(USER_KEY, JSON.stringify(me));

      goStep(3);
      setupKycStep(me.role);
      toast('Phone verified. Upload your KYC document to complete registration.');
    } catch (x) { toast(x.message, 'error'); }
    finally { setBusy(e.currentTarget, false); }
  });

  // ── Step 3 → file dropzone wiring ───────────────────────────
  const fileInput = $('#kyc-file');
  const dropzone  = $('#kyc-dropzone');
  const submitBtn = $('#btn-kyc-submit');

  fileInput?.addEventListener('change', () => {
    const f = fileInput.files[0];
    if (!f) return;
    
    dropzone.classList.add('has-file');
    $('#dz-filename').textContent = `${f.name} (${(f.size / 1024).toFixed(1)} KB)`;
    
    // THE FIX: Lock the button and show the user that cryptography is happening
    const btnToLock = typeof submitBtn !== 'undefined' ? submitBtn : submitKycBtn;
    btnToLock.disabled = true; 
    
    const preview = $('#hash-preview');
    if (preview) {
      preview.style.display = 'block';
      $('#hash-value').innerHTML = '<span style="color:var(--amber)">Computing SHA-256... ⏳</span>';
    }

    hashFile(f).then(h => {
      $('#hash-value').innerHTML = `<span style="color:var(--green)">🔒 ${h}</span>`;
      btnToLock.disabled = false;
    });
  });

  // Drag-and-drop
  dropzone?.addEventListener('dragover',  e => { e.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone?.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone?.addEventListener('drop', e => {
    e.preventDefault(); dropzone.classList.remove('dragover');
    const f = e.dataTransfer.files[0];
    if (f) { fileInput.files = e.dataTransfer.files; fileInput.dispatchEvent(new Event('change')); }
  });

  // ── Step 3 → KYC submit ─────────────────────────────────────
  $('#kyc-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const file    = fileInput?.files[0];
    const serial  = $('#kyc-serial')?.value.trim().toUpperCase() || null;
    const docType = $('#kyc-doc-type').value;
    const meta    = KYC_META[selectedRole || user()?.role];

    if (!file) { toast('Please select a document to upload.', 'error'); return; }
    if (meta?.needSerial && !serial) {
      toast('Serial number is required for your role — check your purchase invoice.', 'error'); return;
    }

    setBusy(e.submitter, true, 'Computing hash and submitting…');
    try {
      const fileHash = await hashFile(file);
      const payload  = { doc_type: docType, file_hash: fileHash };
      if (serial) payload.serial_for_device = serial;

      const d = envelope(await api('/api/v1/kyc/upload', {
        method: 'POST', body: JSON.stringify(payload)
      }));

      $('#kyc-result').innerHTML = `<div class="notice success">
        <strong>Document submitted for admin review.</strong><br>
        Document ID: <span class="mono">${esc(d.doc_id || '—')}</span><br>
        ${d.serial_for_device ? `Serial on file: <span class="mono">${esc(d.serial_for_device)}</span><br>` : ''}
        <small>${esc(d.message || '')}</small>
      </div>`;
      $('#kyc-done').style.display = 'block';
      toast('KYC document submitted.');
    } catch (x) {
      $('#kyc-result').innerHTML = `<div class="notice error">${esc(x.message)}</div>`;
      toast(x.message, 'error');
    } finally { setBusy(e.submitter, false); }
  });
}

function goStep(n) {
  $$('.multi-step').forEach((el, i) => el.classList.toggle('active', i + 1 === n));
  for (let i = 1; i <= 3; i++) {
    const si = $(`#si-${i}`), sl = $(`#sl-${i}`);
    if (!si) continue;
    si.classList.remove('active', 'done');
    if (i < n)       { si.classList.add('done');   sl?.classList.add('done'); }
    else if (i === n){ si.classList.add('active'); }
    else             { sl?.classList.remove('done'); }
  }
}

function setupKycStep(role) {
  const meta = KYC_META[role] || KYC_META['BUYER'];
  $('#kyc-doc-type').value = meta.doc_type;
  const box = $('#kyc-role-box');
  if (box) box.innerHTML = `
    <div style="margin-bottom:12px">
      <strong style="font-family:'Space Grotesk',sans-serif">${esc(meta.label)}</strong>
      <p class="muted" style="font-size:.82rem;margin-top:2px">${esc(role)} account · ${esc(meta.activation || '')}</p>
    </div>
    ${meta.notice}
  `;
  const sw = $('#kyc-serial-wrap');
  if (sw) sw.style.display = meta.needSerial ? 'block' : 'none';
}

/* ═══════════════════════════════════════════════════════════════
   DASHBOARD
══════════════════════════════════════════════════════════════ */
async function loadDashboard() {
  if (!requireAuth()) return;

  let me = user();
  if (!me) {
    try { me = envelope(await api('/api/v1/me')); localStorage.setItem(USER_KEY, JSON.stringify(me)); }
    catch { logout(); return; }
  }

  $('#welcome-name').textContent = me.name || 'Member';
  $('#role-label').innerHTML     = `<small style="font-weight:400;color:var(--muted);font-family:Inter,sans-serif;font-size:.9rem"> · ${esc(me.role)}</small>`;
  $('#account-status').innerHTML = chip(me.status);
  $('#user-id').textContent      = me.user_id || '—';

  // REWARD POINTS
  if (me.reward_points !== undefined) {
    const pts = document.createElement('div');
    pts.style.cssText = 'margin-top:10px;display:flex;align-items:center;gap:8px;';
    pts.innerHTML = `<span style="background:var(--amber-bg);color:#92400e;border:1px solid var(--amber);border-radius:20px;padding:4px 14px;font-size:.82rem;font-weight:600;font-family:'Space Grotesk',sans-serif;">🌱 ${me.reward_points || 0} Eco Points</span><span class="muted" style="font-size:.75rem">Earned on successful device recycling</span>`;
    $('#account-status').parentNode.appendChild(pts);
  }

 const actions = $('#dash-actions');
  if (actions) {
    const btns = [];
    if (['FIRST_BUYER', 'BUYER', 'RESELLER'].includes(me.role))
      btns.push(`<a class="button primary" href="register-device.html">Register device</a>`);
    if (me.role === 'RECYCLER')
      btns.push(`<a class="button primary" href="recycling.html">Recycling desk</a>`);
    if (['FIRST_BUYER', 'BUYER', 'RESELLER'].includes(me.role))
      btns.push(`<a class="button ghost" href="transfer.html">Marketplace</a>`);
      
    // THE FIX: DPDP Act Delete Account Button
    btns.push(`<button class="button danger ghost" id="dpdp-delete-btn" title="DPDP Act Section 12(3) Compliance">Delete Account</button>`);
    
    actions.innerHTML = btns.join('');
    
    // Wire up the delete action
    $('#dpdp-delete-btn')?.addEventListener('click', async (e) => {
      if (!confirm("⚠️ DPDP Right to Erasure: Are you sure you want to permanently delete your personal data? Your on-chain history will be anonymized into a Cryptographic Ghost.")) return;
      
      setBusy(e.target, true, 'Deleting data...');
      try {
        await api('/api/v1/me', { method: 'DELETE' });
        toast('Personal data erased. Complying with DPDP Act.');
        setTimeout(() => logout(true), 1500);
      } catch (err) {
        toast(err.message, 'error');
        setBusy(e.target, false);
      }
    });
  }
  const root = $('#dashboard-content');
  const role = me.role;
  let html = '';

  // THE FIX: Wrapped the entire normal dashboard in an `else` block
  if (me.status !== 'ACTIVE' && role !== 'ADMIN') {
    html = `
      <div class="panel" style="text-align:center; padding: 60px 20px;">
        <div style="font-size:3rem; margin-bottom:16px;">⏳</div>
        <h2>Account Under Review</h2>
        <p class="muted" style="max-width:400px; margin:12px auto; line-height:1.6;">
          Your account status is currently <strong>${esc(me.status)}</strong>. 
          Our admin team is reviewing your documentation. The marketplace and registry features will unlock automatically once you are verified.
        </p>
        <button class="button ghost" onclick="location.reload()" style="margin-top:20px;">Refresh Status</button>
      </div>
    `;
  } else {
    // ---- START OF NORMAL DASHBOARD (Only executes if ACTIVE or ADMIN) ----
    if (['FIRST_BUYER', 'BUYER', 'RESELLER'].includes(role)) {
      html += `
        <div class="panel">
          <div class="panel-head">
            <div><span class="eyebrow">Owned passports</span><h2>My devices</h2></div>
            ${role === 'FIRST_BUYER' ? '<a class="button primary small" href="register-device.html">Register device</a>' : ''}
          </div>
          <div id="my-devices" class="card-grid"><div class="loading">Loading devices…</div></div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <div><span class="eyebrow">Handoffs</span><h2>Incoming transfers</h2></div>
            <button class="button ghost small" id="refresh-incoming">Refresh</button>
          </div>
          <div id="incoming" class="stack"><div class="loading">Loading…</div></div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <div><span class="eyebrow">Disputes</span><h2>My disputes</h2></div>
            <button class="button ghost small" id="open-raise-dispute">Raise dispute</button>
          </div>
          <div id="my-disputes" class="stack"><div class="loading">Loading…</div></div>
        </div>

        <!-- Raise dispute modal -->
        <div id="dispute-modal" style="display:none;position:fixed;inset:0;background:rgba(13,31,23,.7);z-index:500;place-items:center">
          <div class="card" style="max-width:480px;width:90%;margin:auto" onclick="event.stopPropagation()">
            <h3 style="margin-bottom:16px">Raise a dispute</h3>
            <form id="dispute-form" class="form-grid">
              <label>Device ID <span class="req">*</span>
              <input name="device_id" required placeholder="TL-BRAND-SERIAL"
               style="font-family:'Space Mono',monospace" 
               oninput="this.value = this.value.toUpperCase().replace(/[^A-Z0-9-]/g, '')">
              </label>
              <label>Dispute type <span class="req">*</span>
                <select name="dispute_type" required>
                  <option value="">Select type</option>
                  <option value="MISREPRESENTED_SPEC">Misrepresented specification</option>
                  <option value="STOLEN">Stolen device</option>
                  <option value="FAKE_STAMP">Fake verification stamp</option>
                  <option value="OWNERSHIP_DISPUTE">Ownership dispute</option>
                  <option value="OTHER">Other</option>
                </select>
              </label>
              <label>Description <span class="req">*</span>
                <textarea name="description" required minlength="10" placeholder="Describe the issue in detail…"></textarea>
              </label>
              <div style="display:flex;gap:10px;justify-content:flex-end">
                <button class="button ghost" type="button" id="close-dispute-modal">Cancel</button>
                <button class="button danger" type="submit">Submit dispute</button>
              </div>
            </form>
          </div>
        </div>

        <!-- List device modal (DPDPA Consent) -->
        <div id="list-modal" style="display:none;position:fixed;inset:0;background:rgba(13,31,23,.7);z-index:500;place-items:center">
          <div class="card" style="max-width:480px;width:90%;margin:auto" onclick="event.stopPropagation()">
            <h3 style="margin-bottom:16px">List on Marketplace</h3>
            <form id="list-form" class="form-grid">
              <input type="hidden" id="list-device-id" name="device_id">
              <label>Asking price (₹) <span class="muted" style="font-weight:400">(Optional)</span>
                <input name="asking_price" type="number" min="0" placeholder="e.g. 50000">
              </label>
              <label>City <span class="muted" style="font-weight:400">(Optional)</span>
                <input name="city" placeholder="e.g. Pune">
              </label>
              <div class="notice warning" style="margin-top:4px">
                <label style="display:flex;align-items:flex-start;gap:10px;margin-bottom:0;cursor:pointer;">
                  <input type="checkbox" required name="dpdpa_consent"
                    style="width:18px;height:18px;margin-top:2px;cursor:pointer;flex-shrink:0">
                  <span style="font-weight:500;font-size:.85rem;line-height:1.4">
                    <strong>DPDPA Consent:</strong> I agree to share my registered phone number
                    via WhatsApp with interested buyers on the public marketplace.
                  </span>
                </label>
              </div>
              <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:8px">
                <button class="button ghost" type="button" id="close-list-modal">Cancel</button>
                <button class="button primary" type="submit">List device</button>
              </div>
            </form>
          </div>
        </div>
      `;
    }

    if (role === 'VERIFIABLE') {
      html += `
        <div class="panel">
          <div class="panel-head">
            <div><span class="eyebrow">Service centre queue</span><h2>Transfers to complete</h2></div>
            <button class="button ghost small" id="refresh-pending">Refresh</button>
          </div>
          <div id="pending-completion" class="stack"><div class="loading">Loading…</div></div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <div><span class="eyebrow">Physical audit</span><h2>Issue a verification stamp</h2></div>
          </div>
          <div class="panel-body">
            <p class="muted" style="margin-bottom:20px">
              After physically inspecting the device, issue the on-chain stamp. This writes to Algorand.
            </p>
            <form id="stamp-form" class="form-grid" style="max-width:480px">
              <label>Device ID <span class="req">*</span>
              <input name="device_id" required placeholder="TL-BRAND-SERIAL"
              style="font-family:'Space Mono',monospace" 
               oninput="this.value = this.value.toUpperCase().replace(/[^A-Z0-9-]/g, '')">
              </label>
              <button class="button primary" type="submit">Issue stamp on-chain</button>
            </form>
            <div id="stamp-result" style="margin-top:14px"></div>
          </div>
        </div>
      `;
    }

    if (role === 'RECYCLER') {
      html += `
        <div class="panel">
          <div class="panel-head">
            <div><span class="eyebrow">Facility inbox</span><h2>Incoming transfers</h2></div>
            <a class="button primary small" href="recycling.html">Recycling desk</a>
          </div>
          <div id="incoming" class="stack"><div class="loading">Loading…</div></div>
        </div>
      `;
    }

    if (role === 'ADMIN') {
      html += `
        <div class="panel">
          <div class="panel-head">
            <div><span class="eyebrow">Admin console</span><h2>Operations overview</h2></div>
          </div>
          <div class="admin-grid">
            <div class="admin-card" onclick="location.href='#disputes'">
              <span class="eyebrow">Open disputes</span>
              <span class="metric" id="cnt-disputes">—</span>
              <p class="muted">Disputes pending review</p>
            </div>
            <div class="admin-card" onclick="location.href='#kyc'">
              <span class="eyebrow">KYC queue</span>
              <span class="metric" id="cnt-kyc">—</span>
              <p class="muted">Documents awaiting review</p>
            </div>
            <div class="admin-card" onclick="location.href='#approvals'">
              <span class="eyebrow">Pending approvals</span>
              <span class="metric" id="cnt-approvals">—</span>
              <p class="muted">VERIFIABLE / RECYCLER accounts</p>
            </div>
            <div class="admin-card" onclick="location.href='#devices'">
              <span class="eyebrow">Device registry</span>
              <span class="metric" id="cnt-devices">—</span>
              <p class="muted">Total devices on-chain</p>
            </div>
          </div>
        </div>

        <div class="panel" id="disputes">
          <div class="panel-head"><div><span class="eyebrow">Open disputes</span><h2>Dispute queue</h2></div></div>
          <div id="disputes-list" class="stack"><div class="loading">Loading…</div></div>
        </div>
        <div class="panel" id="approvals">
          <div class="panel-head"><div><span class="eyebrow">Pending approvals</span><h2>VERIFIABLE &amp; RECYCLER accounts</h2></div></div>
          <div id="approvals-list" class="stack"><div class="loading">Loading…</div></div>
        </div>
        <div class="panel" id="kyc">
          <div class="panel-head"><div><span class="eyebrow">KYC documents</span><h2>Pending review</h2></div></div>
          <div id="kyc-list" class="stack"><div class="loading">Loading…</div></div>
        </div>
        <div class="panel" id="devices">
          <div class="panel-head"><div><span class="eyebrow">Device registry</span><h2>All devices</h2></div></div>
          <div id="devices-list" class="stack"><div class="loading">Loading…</div></div>
        </div>

        <div class="panel" id="verifiers">
          <div class="panel-head"><div><span class="eyebrow">Verifier oversight</span><h2>Authorized service centers</h2></div></div>
          <div id="verifiers-list" class="stack"><div class="loading">Loading…</div></div>
        </div>
      `;
    }
  } // <--- END OF ELSE BLOCK


  root.innerHTML = html || empty('No workspace', 'Your role does not have a configured dashboard.');
  loadUserNotifications();
  if (['FIRST_BUYER', 'BUYER', 'RESELLER'].includes(role)) {
    loadMyDevices();
    loadIncoming();
    loadMyDisputes();
    wireDisputeModal();
    wireListModal();
    $('#refresh-incoming')?.addEventListener('click', loadIncoming);
  }
  if (role === 'VERIFIABLE') {
    loadPendingCompletion();
    wireStampForm();
    $('#refresh-pending')?.addEventListener('click', loadPendingCompletion);
  }
  if (role === 'RECYCLER') loadIncoming();
  if (role === 'ADMIN')    loadAdminAll();
}

/* ── My devices ──────────────────────────────────────────────── */
async function loadMyDevices() {
  const el = $('#my-devices');
  if (!el) return;
  try {
    const d = envelope(await api('/devices/my-devices'));
    const list = (d.devices || d || []);
    el.innerHTML = list.length
      ? list.map(x => `
        <article class="device-card">
          <div class="device-top">
            <span class="mono">${esc(x.device_id)}</span>
            ${chip(x.status === 'TRANSFERRED' ? 'NEWLY ACQUIRED' : x.status)}
          </div>
          <h3 style="margin:8px 0 6px">${esc(x.brand_name || x.brand || 'Laptop')}</h3>
          <p class="muted" style="font-size:.82rem">
            ${esc(Object.entries(x.current_config || {}).slice(0,3).map(([k,v]) => `${k}: ${v}`).join(' · ') || 'Config on file')}
          </p>
          <p class="muted" style="font-size:.78rem;margin-top:4px">
            Stamp: ${x.stamp_valid
              ? '<span style="color:var(--green)">✓ Valid</span>'
              : '<span style="color:var(--red)">✗ Invalid — re-verify first</span>'}
          </p>
          <div class="card-actions">
            <a class="button ghost small" href="track.html?id=${encodeURIComponent(x.device_id)}">View passport</a>
            ${x.is_for_sale
              ? `<button class="button danger small" data-unlist="${esc(x.device_id)}">Unlist</button>`
              : `<button class="button secondary small" data-list="${esc(x.device_id)}">List for sale</button>`
            }
            <button class="button ghost small" data-transfer="${esc(x.device_id)}">Transfer</button>
          </div>
        </article>`).join('')
      : empty('No devices yet', 'Register a laptop or receive a verified transfer.');

    $$('[data-list]',    el).forEach(b => b.addEventListener('click', () => openListModal(b.dataset.list)));
    $$('[data-unlist]',  el).forEach(b => b.addEventListener('click', () => unlistDevice(b.dataset.unlist, b)));
    $$('[data-transfer]',el).forEach(b => b.addEventListener('click', () => initiateTransfer(b.dataset.transfer, b)));
  } catch (e) {
    el.innerHTML = empty('Unable to load devices', e.message);
  }
}

// Opens the DPDPA consent modal
function openListModal(id) {
  const modal = $('#list-modal');
  if (modal) {
    $('#list-device-id').value = id;
    modal.style.display = 'grid';
  }
}

// Wires the list modal form + DPDPA consent
function wireListModal() {
  const modal = $('#list-modal');
  $('#close-list-modal')?.addEventListener('click', () => { if (modal) modal.style.display = 'none'; });
  modal?.addEventListener('click', e => { if (e.target === modal) modal.style.display = 'none'; });

  $('#list-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const fd    = new FormData(e.target);
    const id    = fd.get('device_id');
    const price = fd.get('asking_price');
    const city  = fd.get('city');

    setBusy(e.submitter, true, 'Listing…');
    try {
      await api(`/transfers/list/${encodeURIComponent(id)}`, {
        method: 'POST',
        body: JSON.stringify({
          asking_price: price ? Number(price) : null,
          city: city || null,
        })
      });
      toast('Device listed on the marketplace.');
      if (modal) modal.style.display = 'none';
      e.target.reset();
      loadMyDevices();
    } catch (x) { toast(x.message, 'error'); }
    finally { setBusy(e.submitter, false); }
  });
}

async function unlistDevice(id, btn) {
  if (!confirm(`Remove ${id} from the marketplace?`)) return;
  setBusy(btn, true, 'Unlisting…');
  try {
    await api(`/transfers/unlist/${encodeURIComponent(id)}`, { method: 'POST' });
    toast('Removed from marketplace.');
    loadMyDevices();
  } catch (e) { toast(e.message, 'error'); }
  finally { setBusy(btn, false); }
}

async function initiateTransfer(id, btn) {
  let to = prompt(`Enter the receiver's Trace-Loop user ID to initiate transfer of ${id}:`);
  if (!to) return;
  
  // PREVENTIVE SANITIZATION: Clean the prompt input so backend doesn't crash on spaces
  to = to.trim().replace(/[^A-Za-z0-9-]/g, '');
  if (to.length < 5) { toast('Invalid User ID format.', 'error'); return; }

  setBusy(btn, true, 'Initiating…');
  try {
    const d = envelope(await api('/transfers/initiate', {
      method: 'POST', body: JSON.stringify({ device_id: id, to_user_id: to })
    }));
    toast(`Transfer initiated. ID: ${d.transfer_id}`);
    loadMyDevices();
    loadIncoming();
  } catch (e) { toast(e.message, 'error'); }
  finally { setBusy(btn, false); }
}

/* ── Incoming transfers ──────────────────────────────────────── */
async function loadIncoming() {
  const el = $('#incoming');
  if (!el) return;
  try {
    const d = envelope(await api('/transfers/incoming'));
    const list = d.incoming_transfers || d || [];
    el.innerHTML = list.length
      ? list.map(x => `
        <article class="transfer-row">
          <div>
            <span class="mono">${esc(x.device_id)}</span>
            <h3>${esc(x.brand_name || 'Device')}</h3>
            <p class="muted" style="font-size:.82rem">From ${esc(x.from_user_id)} · ${fmtDate(x.initiated_at)}</p>
          </div>
          <div class="row-actions">
            ${chip(x.status)}
            ${x.status === 'PENDING'
              ? `<button class="button primary small" data-accept="${esc(x.transfer_id)}">Accept</button>
                 <button class="button ghost small"   data-cancel="${esc(x.transfer_id)}">Cancel</button>`
              : ''
            }
          </div>
        </article>`).join('')
      : empty('No incoming transfers', 'Accepted handoffs will appear here.');

    $$('[data-accept]', el).forEach(b => b.addEventListener('click', () => transferAction(b.dataset.accept, 'accept', b)));
    $$('[data-cancel]', el).forEach(b => b.addEventListener('click', () => transferAction(b.dataset.cancel, 'cancel', b)));
  } catch (e) {
    el.innerHTML = empty('Unable to load transfers', e.message);
  }
}

async function transferAction(id, action, btn) {
  setBusy(btn, true, 'Processing…');
  try {
    await api(`/transfers/${id}/${action}`, { method: 'POST' });
    if (action === 'accept') {
      toast('Accepted! Take the device to an authorized Service Centre for the final physical audit.', 'info');
    } else {
      toast(`Transfer ${action}ed.`);
    }
    loadIncoming();
    if (action === 'accept') loadMyDevices();
  } catch (e) { toast(e.message, 'error'); }
  finally { setBusy(btn, false); }
}

/* ── My disputes ─────────────────────────────────────────────── */
async function loadMyDisputes() {
  const el = $('#my-disputes');
  if (!el) return;
  try {
    const d = envelope(await api('/disputes/my'));
    const list = d.disputes || d || [];
    el.innerHTML = list.length
      ? list.map(x => `
        <div class="transfer-row">
          <div>
            <span class="mono">${esc(x.device_id)}</span>
            <h3>${esc(String(x.dispute_type || '').replace(/_/g, ' '))}</h3>
            <p class="muted" style="font-size:.82rem">${esc(x.description || '')} · ${fmtDate(x.created_at)}</p>
          </div>
          <div class="row-actions">${chip(x.status)}</div>
        </div>`).join('')
      : empty('No disputes', 'Disputes you raise will appear here.');
  } catch (e) { el.innerHTML = empty('Unable to load disputes', e.message); }
}

function wireDisputeModal() {
  const modal = $('#dispute-modal');
  $('#open-raise-dispute')?.addEventListener('click', () => { if (modal) modal.style.display = 'grid'; });
  $('#close-dispute-modal')?.addEventListener('click', () => { if (modal) modal.style.display = 'none'; });
  modal?.addEventListener('click', e => { if (e.target === modal) modal.style.display = 'none'; });

  $('#dispute-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    setBusy(e.submitter, true, 'Submitting…');
    try {
      await api('/disputes/raise', {
        method: 'POST',
        body: JSON.stringify(Object.fromEntries(fd.entries()))
      });
      toast('Dispute submitted and permanently attached to device history.');
      if (modal) modal.style.display = 'none';
      e.target.reset();
      loadMyDisputes();
    } catch (x) { toast(x.message, 'error'); }
    finally { setBusy(e.submitter, false); }
  });
}

/* ── VERIFIABLE: stamp ───────────────────────────────────────── */
function wireStampForm() {
  $('#stamp-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const id = new FormData(e.target).get('device_id')?.trim().toUpperCase();
    if (!id) { toast('Enter a device ID.', 'error'); return; }
    setBusy(e.submitter, true, 'Writing to chain…');
    try {
      const d = envelope(await api('/verification/stamp', {
        method: 'POST', body: JSON.stringify({ device_id: id })
      }));
      const tx = d.chain_tx_id || d.tx_id || '';
      $('#stamp-result').innerHTML = `<div class="notice success">
        Stamp issued on Algorand. ${tx
          ? `<a href="https://lora.algokit.io/testnet/transaction/${encodeURIComponent(tx)}"
               target="_blank" rel="noopener">View on Lora ↗</a>`
          : ''}
      </div>`;
      e.target.reset();
      loadPendingCompletion();
    } catch (x) {
      $('#stamp-result').innerHTML = `<div class="notice error">${esc(x.message)}</div>`;
      toast(x.message, 'error');
    } finally { setBusy(e.submitter, false); }
  });
}

async function loadPendingCompletion() {
  const el = $('#pending-completion');
  if (!el) return;
  try {
    const d = envelope(await api('/transfers/pending-completion'));
    const list = d.transfers_to_complete || d || [];
    el.innerHTML = list.length
      ? list.map(x => `
        <article class="transfer-row">
          <div>
            <span class="mono">${esc(x.device_id)}</span>
            <h3>${esc(x.brand_name || 'Device')}</h3>
            <p class="muted" style="font-size:.82rem">To ${esc(x.to_user_id)} · ${fmtDate(x.initiated_at)}</p>
          </div>
          <div class="row-actions">
            ${chip(x.stamp_valid ? 'VERIFIED' : 'NEEDS STAMP')}
            <button class="button primary small" data-complete="${esc(x.transfer_id)}">Complete transfer</button>
          </div>
        </article>`).join('')
      : empty('Queue is clear', 'Transfers awaiting physical inspection appear here.');

    $$('[data-complete]', el).forEach(b => b.addEventListener('click', async () => {
      setBusy(b, true, 'Executing…');
      try {
        const d = envelope(await api(`/transfers/${b.dataset.complete}/complete`, { method: 'POST' }));
        const tx = d.chain_tx_id || d.tx_id || '';
        toast(`Transfer complete!${tx ? ` TX: ${tx.slice(0, 16)}…` : ''}`);
        loadPendingCompletion();
      } catch (e) { toast(e.message, 'error'); }
      finally { setBusy(b, false); }
    }));
  } catch (e) {
    el.innerHTML = empty('Unable to load queue', e.message);
  }
}

/* ── ADMIN ───────────────────────────────────────────────────── */
async function loadAdminAll() {
  await Promise.all([loadAdminDisputes(), loadAdminApprovals(), loadAdminKyc(), loadAdminDevices(), loadAdminVerifiers()]);
}

async function loadAdminDisputes() {
  const el = $('#disputes-list');
  if (!el) return;
  try {
    const d = envelope(await api('/disputes/open'));
    const list = d.disputes || d || [];
    $('#cnt-disputes').textContent = Array.isArray(list) ? list.length : (d.total ?? '—');
    el.innerHTML = list.length
      ? list.map(x => `
        <div class="transfer-row">
          <div>
            <span class="mono">${esc(x.device_id)}</span>
            <h3>${esc(String(x.dispute_type || '').replace(/_/g, ' '))}</h3>
            <p class="muted" style="font-size:.82rem">${esc(x.description || '')} · Raised by ${esc(x.raised_by || '—')}</p>
          </div>
          <div class="row-actions">
            ${chip(x.status)}
            ${x.status === 'OPEN'
              ? `<button class="button primary small" data-resolve="${esc(x.dispute_id || x.id)}">Resolve</button>`
              : ''}
          </div>
        </div>`).join('')
      : empty('No open disputes', 'All disputes resolved.');
    $$('[data-resolve]', el).forEach(b => b.addEventListener('click', () => adminResolveDispute(b.dataset.resolve)));
  } catch (e) { el.innerHTML = empty('Error', e.message); }
}

async function adminResolveDispute(id) {
  const note  = prompt('Resolution note (required):');
  if (!note) return;
  const state = prompt('Resolve to state: REGISTERED / VERIFIED / TRANSFERRED', 'REGISTERED');
  if (!['REGISTERED', 'VERIFIED', 'TRANSFERRED'].includes(state?.toUpperCase())) {
    toast('Invalid state.', 'error'); return;
  }
  try {
    await api(`/disputes/${id}/resolve`, {
      method: 'PATCH',
      body: JSON.stringify({ resolution_note: note, resolved_state: state.toUpperCase() })
    });
    toast('Dispute resolved.');
    loadAdminDisputes();
  } catch (e) { toast(e.message, 'error'); }
}

async function loadAdminApprovals() {
  const el = $('#approvals-list');
  if (!el) return;
  try {
    const d = envelope(await api('/admin/approvals/pending'));
    
    // ── BULLETPROOF ARRAY EXTRACTION ──
    // This automatically finds the array regardless of what key the backend uses
    const list = d.approvals || d.users || d.data || d.items || (Array.isArray(d) ? d : Object.values(d).find(Array.isArray) || []);
    
    $('#cnt-approvals').textContent = list.length;
    
    el.innerHTML = list.length
      ? list.map(x => `
        <div class="transfer-row">
          <div>
            <strong>${esc(x.name || '—')}</strong>
            <p class="muted" style="font-size:.82rem">${esc(x.role)} · ${esc(x.phone || '—')}</p>
            ${x.brand_auth_code ? `<p class="muted" style="font-size:.78rem">Brand auth: ${esc(x.brand_auth_code)}</p>` : ''}
            ${x.cpcb_number     ? `<p class="muted" style="font-size:.78rem">CPCB: ${esc(x.cpcb_number)}</p>` : ''}
          </div>
          <div class="row-actions">
            <button class="button primary small" data-approve="${esc(x.user_id || x.id)}">Approve</button>
            <button class="button danger small"  data-reject="${esc(x.user_id || x.id)}">Reject</button>
          </div>
        </div>`).join('')
      : empty('No pending approvals', 'All accounts processed.');
      
    $$('[data-approve]', el).forEach(b => b.addEventListener('click', () => adminApprove(b.dataset.approve, 'approve', b)));
    $$('[data-reject]',  el).forEach(b => b.addEventListener('click', () => adminApprove(b.dataset.reject, 'reject', b)));
  } catch (e) { el.innerHTML = empty('Error', e.message); }
}

// Replace this function
async function adminApprove(id, action, btn) {
  setBusy(btn, true, 'Working…');
  try {
    await api(`/admin/approvals/${id}/${action}`, { 
      method: 'POST',
      body: JSON.stringify({ notes: "Admin reviewed via dashboard" }) 
    });
    toast(`Account ${action}d.`);
    loadAdminApprovals();
  } catch (e) { toast(e.message, 'error'); }
  finally { setBusy(btn, false); }
}
/* ── ADMIN KYC MANAGEMENT ────────────────────────────────────── */
async function loadAdminKyc() {
  const el = $('#kyc-list');
  if (!el) return;
  try {
    const d = envelope(await api('/admin/kyc/pending'));
    
    // Explicitly targets 'pending_documents' from your Python output
    const list = d.pending_documents || d.documents || (Array.isArray(d) ? d : Object.values(d).find(Array.isArray) || []);
    
    $('#cnt-kyc').textContent = list.length;
    
    el.innerHTML = list.length
      ? list.map(x => `
        <div class="transfer-row">
          <div>
            <strong>${esc(x.doc_type || 'Document')}</strong>
            <p class="muted" style="font-size:.82rem">User ${esc(x.user_id)} · ${fmtDate(x.uploaded_at || x.created_at)}</p>
            ${x.serial_for_device ? `<p class="muted" style="font-size:.78rem">Serial on file: <span class="mono" style="color:var(--green)">${esc(x.serial_for_device)}</span></p>` : ''}
            ${x.file_hash ? `<p class="muted" style="font-size:.75rem;font-family:'Space Mono',monospace;margin-top:4px;">🔒 Hash: <span style="color:var(--green)">${esc(x.file_hash.slice(0, 24))}...</span></p>` : ''}
          </div>
          <div class="row-actions">
            ${chip(x.status || 'PENDING')}
            <button class="button primary small" data-kyc-approve="${esc(x.doc_id || x.id)}">Approve</button>
            <button class="button danger small"  data-kyc-reject="${esc(x.doc_id || x.id)}">Reject</button>
          </div>
        </div>`).join('')
      : empty('KYC queue is clear', 'No documents pending review.');

    $$('[data-kyc-approve]', el).forEach(b => b.addEventListener('click', () => adminKycReview(b.dataset.kycApprove, 'approve', b)));
    $$('[data-kyc-reject]',  el).forEach(b => b.addEventListener('click', () => adminKycReview(b.dataset.kycReject, 'reject', b)));
  } catch (e) { el.innerHTML = empty('Error', e.message); }
}

// Replace this function
// THE FIX: Added 'btn' parameter
async function adminKycReview(docId, action, btn) {
  const note = action === 'reject' ? prompt('Rejection reason (required):') : null;
  
  if (action === 'reject' && !note) {
    toast('Rejection reason is required.', 'error');
    return;
  }

  const decisionVal = action === 'approve' ? 'ACCEPT' : 'REJECT';
  
  // Build payload dynamically so we don't send nulls to strict Pydantic models
  const payload = { decision: decisionVal };
  if (note) payload.rejection_reason = note;

  // THE FIX: Lock the button to prevent duplicate API clicks
  setBusy(btn, true, 'Processing…');
  try {
    await api(`/admin/kyc/${docId}/review`, {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    
    toast(`Document successfully ${action}d.`);
    loadAdminKyc();
  } catch (e) { 
    toast(e.message, 'error'); 
  } finally { 
    // THE FIX: Always unlock the button, even if the API fails
    setBusy(btn, false); 
  }
}

async function loadAdminDevices() {
  const el = $('#devices-list');
  if (!el) return;
  try {
    const d = envelope(await api('/admin/devices'));
    const list = d.devices || d || [];
    $('#cnt-devices').textContent = Array.isArray(list) ? list.length : (d.total ?? '—');
    el.innerHTML = list.length
      ? list.map(x => `
        <div class="transfer-row">
          <div>
            <span class="mono">${esc(x.device_id || x.id)}</span>
            <h3>${esc(x.brand_name || x.brand || 'Device')}</h3>
            <p class="muted" style="font-size:.82rem">Owner: ${esc(x.current_owner_id || '—')} · ${fmtDate(x.registered_at)}</p>
          </div>
          <div class="row-actions">
            ${chip(x.status)}
            <a class="button ghost small" href="track.html?id=${encodeURIComponent(x.device_id || x.id)}">Passport</a>
            ${!['RECYCLED', 'EXPORTED'].includes(x.status)
              ? `<button class="button danger small" data-export="${esc(x.device_id || x.id)}">Export</button>`
              : ''}
          </div>
        </div>`).join('')
      : empty('No devices', 'No devices in the registry.');
    $$('[data-export]', el).forEach(b => b.addEventListener('click', async () => {
      if (!confirm(`Mark ${b.dataset.export} as EXPORTED? This is terminal.`)) return;
      try {
        await api(`/admin/devices/${encodeURIComponent(b.dataset.export)}/export`, { method: 'POST' });
        toast('Device marked as EXPORTED.');
        loadAdminDevices();
      } catch (e) { toast(e.message, 'error'); }
    }));
  } catch (e) { el.innerHTML = empty('Error', e.message); }
}

/* ═══════════════════════════════════════════════════════════════
   TRACK
══════════════════════════════════════════════════════════════ */
async function initTrack() {
  const id = new URLSearchParams(location.search).get('id');
  if (id) { $('#device-id').value = id; searchDevice(); }
  $('#track-form')?.addEventListener('submit', e => { e.preventDefault(); searchDevice(); });
}

async function searchDevice() {
  const inputEl = $('#device-id');
  if (!inputEl) return;

  // THE FIX: Instantly self-heal and sanitize the input field value
  inputEl.value = inputEl.value.trim().toUpperCase().replace(/[^A-Z0-9-]/g, '');
  const id = inputEl.value;

  if (!id) { toast('Enter a device ID.', 'error'); return; }

  const r = $('#track-result');
  r.innerHTML = `<div class="loading">Loading passport for <span class="mono">${esc(id)}</span>…</div>`;

  try {
    const opts = token() ? {} : { auth: false };
    const [dv, tr, di, ev, st] = await Promise.allSettled([
      api(`/devices/${encodeURIComponent(id)}`, opts),
      api(`/transfers/device/${encodeURIComponent(id)}`, opts),
      api(`/disputes/device/${encodeURIComponent(id)}`, opts),
      api(`/ledger/device/${encodeURIComponent(id)}/events`, opts),
      api(`/ledger/device/${encodeURIComponent(id)}/state`, opts),
    ]);

    const d      = dv.status === 'fulfilled' ? envelope(dv.value) : {};
    const tx     = tr.status === 'fulfilled' ? envelope(tr.value) : {};
    const ds     = di.status === 'fulfilled' ? envelope(di.value) : {};
    const events = ev.status === 'fulfilled' ? envelope(ev.value) : {};
    const state  = st.status === 'fulfilled' ? envelope(st.value) : {};

    const transfers = tx.transfers || tx.history || (Array.isArray(tx) ? tx : []);
    const disputes  = ds.disputes  || (Array.isArray(ds) ? ds : []);
    const evList    = events.events || (Array.isArray(events) ? events : []);

    r.innerHTML = `
      <div class="passport-header">
        <div>
          <span class="eyebrow">Digital Product Passport</span>
          <h2>${esc(d.brand_name || d.brand || 'Device passport')}</h2>
          <p class="mono" style="margin-top:4px">${esc(d.device_id || id)}</p>
        </div>
        ${chip(d.status || d.db_status || 'UNKNOWN')}
      </div>

      <div class="stat-grid">
        <div><small>Database state</small><strong>${esc(d.status || d.db_status || '—')}</strong></div>
        <div><small>Chain state</small><strong>${esc(state.state_label || d.chain_status || '—')}</strong></div>
        <div><small>Stamp valid</small><strong style="color:${d.stamp_valid ? 'var(--green)' : 'var(--red)'}">
          ${d.stamp_valid ? '✓ Yes' : '✗ No'}
        </strong></div>
        <div><small>Registered</small><strong>${fmtDate(d.registered_at)}</strong></div>
      </div>

      <div class="passport-columns">
        <section>
          <div class="section-label">Configuration</div>
          <pre>${esc(JSON.stringify(d.current_config || {}, null, 2))}</pre>
        </section>
        <section>
          <div class="section-label">Ownership chain</div>
          <div class="mini-list">
            ${transfers.length
              ? transfers.map(x => `<div class="mini-row">
                  <strong>${esc(x.status || x.event_type || 'Record')}</strong>
                  <span>${fmtDate(x.initiated_at || x.created_at || x.timestamp)}</span>
                </div>`).join('')
              : '<p class="muted">No transfer records yet.</p>'
            }
          </div>
        </section>
        <section>
          <div class="section-label">Disputes</div>
          <div class="mini-list">
            ${disputes.length
              ? disputes.map(x => `<div class="mini-row">
                  <strong>${esc(String(x.dispute_type || '').replace(/_/g, ' ') || 'Dispute')}</strong>
                  <span>${chip(x.status)}</span>
                </div>`).join('')
              : '<p class="muted">No disputes on record.</p>'
            }
          </div>
        </section>
      </div>

      <div class="ledger">
        <div class="section-label">Ledger events</div>
        ${renderEvents(evList)}
      </div>
    `;
  } catch (e) {
    r.innerHTML = empty('Passport unavailable', e.message);
  }
}

function renderEvents(list) {
  if (!Array.isArray(list) || !list.length) return '<p class="muted">No ledger events returned.</p>';
  return `<div class="event-list">${list.map(x => {
    const tx = x.tx_hash || x.tx_id || x.transaction_id || '';
    return `<div class="event-row">
      <span class="event-dot"></span>
      <div>
        <strong>${esc(x.event_type || x.event || 'Event')}</strong>
        <p>${fmtDate(x.timestamp || x.created_at)}
          ${tx ? `· <a href="https://lora.algokit.io/testnet/transaction/${encodeURIComponent(tx)}"
               target="_blank" rel="noopener">View on Lora ↗</a>` : ''}
        </p>
      </div>
    </div>`;
  }).join('')}</div>`;
}

/* ═══════════════════════════════════════════════════════════════
   MARKETPLACE  (WhatsApp contact — your version)
══════════════════════════════════════════════════════════════ */
let allListings = [];

async function initTransfer() {
  const g = $('#marketplace');
  try {
    const d = envelope(await api('/transfers/marketplace', { auth: false }));
    allListings = d.listings || (Array.isArray(d) ? d : []);
    renderListings(allListings);
    $('#listing-count').textContent = `${allListings.length} verified listing${allListings.length !== 1 ? 's' : ''}`;
  } catch (e) {
    g.innerHTML = empty('Marketplace unavailable', e.message);
  }

  $('#filter-brand')?.addEventListener('change', applyFilters);
  $('#filter-sort')?.addEventListener('change', applyFilters);

  $('#modal-track-btn')?.addEventListener('click', () => {
    const id = $('#modal-device-id').textContent;
    if (id) location.href = `track.html?id=${encodeURIComponent(id)}`;
  });
}

function applyFilters() {
  let list = [...allListings];
  const brand = $('#filter-brand')?.value;
  const sort  = $('#filter-sort')?.value;
  if (brand) list = list.filter(x => x.brand_code === brand || x.device_id?.includes(`-${brand}-`));
  if (sort === 'price-asc')  list.sort((a, b) => (a.asking_price || 0) - (b.asking_price || 0));
  if (sort === 'price-desc') list.sort((a, b) => (b.asking_price || 0) - (a.asking_price || 0));
  renderListings(list);
  $('#listing-count').textContent = `${list.length} listing${list.length !== 1 ? 's' : ''}`;
}

function renderListings(list) {
  const g = $('#marketplace');
  g.innerHTML = list.length
    ? list.map(x => `
      <article class="listing">
        <div class="listing-image">${esc((x.brand_code || x.brand || 'TL').slice(0, 2).toUpperCase())}</div>
        <div class="listing-body">
          <div class="device-top" style="margin-bottom:8px">
            <span class="mono">${esc(x.device_id)}</span>
            ${chip('VERIFIED')}
          </div>
          <h3>${esc(x.brand_name || x.brand || 'Laptop')}</h3>
          <p class="muted" style="font-size:.82rem;margin:6px 0 12px">
            ${esc(Object.entries(x.current_config || {}).slice(0, 3).map(([k, v]) => `${k}: ${v}`).join(' · ') || 'Verified configuration on file')}
          </p>
          <div class="listing-meta">
            <strong>${money(x.asking_price)}</strong>
            <span>⌖ ${esc(x.city || 'Location undisclosed')}</span>
          </div>
          <div style="display:flex;gap:8px">
            <button class="button secondary" style="flex:1"
              data-contact="${esc(x.device_id)}"
              data-owner="${esc(x.seller_phone || x.current_owner_phone || '')}">
              Contact seller
            </button>
            <a class="button ghost" href="track.html?id=${encodeURIComponent(x.device_id)}">Passport</a>
          </div>
        </div>
      </article>`).join('')
    : empty('Marketplace is quiet', 'Verified devices listed for transfer will appear here.');

  $$('[data-contact]').forEach(b =>
    b.addEventListener('click', () => showContactModal(b.dataset.contact, b.dataset.owner))
  );
}

function showContactModal(deviceId, sellerPhone) {
  const modal = $('#contact-modal');
  if (!modal) return;

  $('#modal-device-id').textContent = deviceId;

  // Clean the phone for WhatsApp — strip everything except digits
  const cleanPhone = (sellerPhone || '').replace(/[^0-9]/g, '');
  const waMsg = encodeURIComponent(
    `Hi! I'm interested in buying your device (${deviceId}) listed on the Trace-Loop marketplace.`
  );

  const container = $('#modal-seller-id');
  if (container) {
    if (cleanPhone) {
      container.innerHTML = `
        <a href="https://wa.me/${cleanPhone}?text=${waMsg}"
           target="_blank" rel="noopener"
           class="button primary full"
           style="background:#25D366;color:#fff;border-color:#25D366;justify-content:center;">
          <svg style="width:18px;height:18px;margin-right:8px;flex-shrink:0" fill="currentColor" viewBox="0 0 24 24">
            <path d="M12.031 0C5.383 0 0 5.38 0 12.03c0 2.126.554 4.197 1.605 6.015L.234 23.418l5.503-1.442a11.97 11.97 0 0 0 6.294 1.772h.004c6.647 0 12.028-5.38 12.028-12.029 0-3.222-1.254-6.252-3.53-8.532A11.974 11.974 0 0 0 12.031 0zm7.15 17.294c-.3.844-1.733 1.62-2.4 1.733-.62.105-1.423.2-3.89-1.272-3.486-2.083-5.748-5.63-5.935-5.88-.188-.25-1.417-1.884-1.417-3.595 0-1.71.885-2.553 1.2-2.884.314-.332.684-.416.91-.416.226 0 .452 0 .64.01.196.01.46-.076.718.547.272.656.93 2.27.973 2.395.068.204.16.48-.008.81-.166.332-.254.538-.5.83-.244.292-.516.626-.737.81-.24.204-.492.427-.215.903.277.476 1.233 2.035 2.646 3.293 1.82 1.622 3.323 2.122 3.792 2.327.468.204.743.166 1.02-.15.278-.316 1.19-1.39 1.51-1.87.32-.476.635-.395 1.055-.24.422.156 2.668 1.257 3.125 1.488.457.23.764.326.87.538.113.19.113 1.11-.186 1.954z"/>
          </svg>
          Chat on WhatsApp
        </a>`;
      container.style.background = 'transparent';
      container.style.padding = '0';
    } else {
      // Fallback if backend doesn't expose phone yet — show Trace-Loop contact token
      container.innerHTML = '';
      container.style.cssText = '';
      container.textContent = `TL-SELLER-${btoa(deviceId).replace(/[^A-Z0-9]/gi, '').slice(0, 12).toUpperCase()}`;
    }
  }

  modal.style.display = 'grid';
}
/* ═══════════════════════════════════════════════════════════════
   REGISTER DEVICE (Advanced Dual-Flow with Secondary KYC)
══════════════════════════════════════════════════════════════ */
async function initRegisterDevice() {
  if (!requireAuth(['FIRST_BUYER'])) return;

  const statusEl = $('#kyc-status-check');
  const submitBtn = $('#btn-register');
  const mainForm = $('#device-form');

  // Live KYC pre-flight check for initial account status
  try {
    const me = envelope(await api('/api/v1/me'));
    if (me.status === 'ACTIVE') {
      statusEl.className = 'check-status ok';
      statusEl.innerHTML = '✓ Account active. Make sure the serial below matches your admin-approved purchase proof.';
    } else if (me.status === 'KYC_IN_PROGRESS') {
      statusEl.className = 'check-status blocked';
      statusEl.innerHTML = `⛔ Your KYC document is still pending admin approval (status: <strong>${esc(me.status)}</strong>).
        Device registration is locked until admin approves your purchase proof.`;
      submitBtn.disabled = true;
    } else {
      statusEl.className = 'check-status blocked';
      statusEl.innerHTML = `⛔ Account status: <strong>${esc(me.status)}</strong>. Complete KYC first.`;
      submitBtn.disabled = true;
    }
  } catch {
    statusEl.className = 'check-status blocked';
    statusEl.textContent = 'Could not verify account status. Are you signed in?';
  }

  // ── PREVIEW & SMART AUTO-FILL ──
  function updatePreview() {
    const code   = ($('#brand_code')?.value || '??').toUpperCase();
    const serial = ($('#serial_raw')?.value || 'SERIAL').toUpperCase();
    const el = $('#device-id-preview');
    if (el) el.textContent = `TL-${code}-${serial}`;
  }

  // Brand code → auto-fill brand name & Preview
  const brandNames = { DL:'Dell', HP:'HP', LN:'Lenovo', AS:'Asus', AC:'Acer', AP:'Apple', MS:'MSI', SG:'Samsung' };
  $('#brand_code')?.addEventListener('change', e => {
    $('#brand_name').value = brandNames[e.target.value] || '';
    updatePreview();
  });

  $('#serial_raw')?.addEventListener('input', updatePreview);

  // Smart auto-fill from notifications
  const urlParams = new URLSearchParams(window.location.search);
  const prefillSerial = urlParams.get('serial');
  if (prefillSerial && $('#serial_raw')) {
    $('#serial_raw').value = prefillSerial;
    updatePreview(); 
    $('#serial_raw').style.boxShadow = "0 0 0 2px var(--green)";
    setTimeout(() => { $('#serial_raw').style.boxShadow = "none"; }, 1500);
  }

  // ── Form submit & Validation handler ──────────────────────────
  mainForm?.addEventListener('submit', async e => {
    e.preventDefault();
    
    // Custom Validation (Because we added 'novalidate' to the HTML)
    if (!mainForm.checkValidity()) {
      toast('Please fill out all required fields marked with an asterisk (*).', 'error');
      mainForm.classList.add('show-validation'); // Triggers the red CSS borders
      return; // Stop execution
    }

    const fd = new FormData(mainForm);
    const serial = fd.get('serial_raw')?.trim().toUpperCase();

    const config = {};
    ['processor', 'ram', 'storage', 'condition', 'display', 'gpu'].forEach(k => {
      if (fd.get(k)?.trim()) config[k] = fd.get(k).trim();
    });

    setBusy(submitBtn, true, 'Registering on Algorand…');
    const result = $('#register-result');
    
    try {
      const d = envelope(await api('/devices/register', {
        method: 'POST',
        body: JSON.stringify({
          brand_code: fd.get('brand_code').trim().toUpperCase(),
          brand_name: fd.get('brand_name').trim(),
          serial_raw: serial,
          original_config: config,
        })
      }));
      
      const tx = d.chain_tx_id || d.tx_id || '';
      result.innerHTML = `<div class="notice success">
        <strong>Device passport created!</strong><br>
        ID: <span class="mono">${esc(d.device_id)}</span> · ${chip(d.status || 'REGISTERED')}<br>
        ${tx ? `Algorand TX: <a href="https://lora.algokit.io/testnet/transaction/${encodeURIComponent(tx)}" target="_blank" rel="noopener">${esc(tx.slice(0, 24))}… ↗</a>` : ''}
      </div>
      <div style="display:flex;gap:10px;margin-top:12px;flex-wrap:wrap">
        <a class="button primary" href="dashboard.html">View my devices</a>
        <a class="button ghost" href="track.html?id=${encodeURIComponent(d.device_id)}">View passport</a>
      </div>`;
      toast(`Passport ${d.device_id} registered.`);
      mainForm.reset();
      updatePreview();
      
    } catch (x) {
      // ── THE MAGIC UX FLOW: Intercept missing proof error ──
      if (x.message.toLowerCase().includes('proof') || x.message.toLowerCase().includes('kyc')) {
        result.innerHTML = ''; // Clear main form error
        toast('Missing invoice for this specific device.', 'error');
        
        // Reveal the dynamic KYC upload UI
        $('#new-kyc-section').style.display = 'block';
        $('#kyc-serial-display').textContent = serial;
        
        // Disable main inputs to lock the flow and prevent duplicate submissions
        submitBtn.disabled = true;
        $$('input, select', mainForm).forEach(el => el.disabled = true);
        
        // Smooth scroll to the upload box
        $('#new-kyc-section').scrollIntoView({ behavior: 'smooth' });
      } else {
        // Standard error handling for other backend errors
        result.innerHTML = `<div class="notice error">${esc(x.message)}</div>`;
        toast(x.message, 'error');
      }
    } finally { 
      setBusy(submitBtn, false); 
    }
  });

  // ── SECONDARY KYC UPLOAD LOGIC ──
  const fileInput = $('#kyc-file');
  const dropzone  = $('#kyc-dropzone');
  const submitKycBtn = $('#btn-new-kyc');

  fileInput?.addEventListener('change', () => {
    const f = fileInput.files[0];
    if (!f) return;
    
    dropzone.classList.add('has-file');
    $('#dz-filename').textContent = `${f.name} (${(f.size / 1024).toFixed(1)} KB)`;
    
    // THE FIX: Lock the button and show the user that cryptography is happening
    const btnToLock = typeof submitBtn !== 'undefined' ? submitBtn : submitKycBtn;
    btnToLock.disabled = true; 
    
    const preview = $('#hash-preview');
    if (preview) {
      preview.style.display = 'block';
      $('#hash-value').innerHTML = '<span style="color:var(--amber)">Computing SHA-256... ⏳</span>';
    }

    hashFile(f).then(h => {
      $('#hash-value').innerHTML = `<span style="color:var(--green)">🔒 ${h}</span>`;
      btnToLock.disabled = false;
    });
  });

  // Drag and drop events
  dropzone?.addEventListener('dragover',  e => { e.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone?.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone?.addEventListener('drop', e => {
    e.preventDefault(); dropzone.classList.remove('dragover');
    const f = e.dataTransfer.files[0];
    if (f) { fileInput.files = e.dataTransfer.files; fileInput.dispatchEvent(new Event('change')); }
  });

  // Submit the new invoice
  $('#new-kyc-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const file = fileInput?.files[0];
    const serialTarget = $('#kyc-serial-display').textContent;

    if (!file) { toast('Please select an invoice to upload.', 'error'); return; }

    setBusy(e.submitter, true, 'Submitting invoice…');
    try {
      const fileHash = await hashFile(file);
      const d = envelope(await api('/api/v1/kyc/upload', {
        method: 'POST',
        body: JSON.stringify({
          doc_type: 'PURCHASE_PROOF',
          file_hash: fileHash,
          serial_for_device: serialTarget
        })
      }));

      // Lock the UI on success
      $('#new-kyc-form').style.display = 'none';
      $('#new-kyc-result').innerHTML = `
        <div class="notice success">
          <strong>Invoice submitted successfully!</strong><br>
          Admin must approve the invoice for <span class="mono">${esc(serialTarget)}</span> before you can register this device.
          <br>Document ID: <span class="mono">${esc(d.doc_id)}</span>
        </div>
        <div style="margin-top:16px"><a class="button ghost" href="dashboard.html">Return to Dashboard</a></div>
      `;
      toast('Purchase proof submitted for review.');
      
    } catch (x) {
      $('#new-kyc-result').innerHTML = `<div class="notice error">${esc(x.message)}</div>`;
      toast(x.message, 'error');
    } finally { setBusy(e.submitter, false); }
  });

} // <--- Notice how this ONE bracket now cleanly encloses the entire function.
  // ----------------------------------------
/* ═══════════════════════════════════════════════════════════════
   RECYCLING
══════════════════════════════════════════════════════════════ */
async function initRecycling() {
  if (!requireAuth(['RECYCLER'])) return;

  $('#receive-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    setBusy(e.submitter, true, 'Acknowledging…');
    try {
      const d = envelope(await api('/recycling/receive', {
        method: 'POST',
        body: JSON.stringify({
          device_id:       f.get('device_id').trim().toUpperCase(),
          condition_notes: f.get('condition_notes') || null,
        })
      }));
      $('#receive-result').innerHTML = `<div class="notice success">${esc(d.message || 'Receipt acknowledged.')}</div>`;
      e.target.reset();
      loadRecyclerIncoming();
    } catch (x) {
      $('#receive-result').innerHTML = `<div class="notice error">${esc(x.message)}</div>`;
      toast(x.message, 'error');
    } finally { setBusy(e.submitter, false); }
  });

  $('#complete-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const id = new FormData(e.target).get('device_id')?.trim().toUpperCase();
    if (!confirm(`Permanently recycle ${id}? This writes a terminal state to Algorand and cannot be undone.`)) return;
    
    setBusy(e.submitter, true, 'Writing to chain…');
    
    try {
      const d = envelope(await api('/recycling/complete', {
        method: 'POST', body: JSON.stringify({ device_id: id })
      }));
      
      // --- THE CRITICAL FIX: Refresh local session so Eco Points update ---
      try {
        const freshMe = envelope(await api('/api/v1/me'));
        localStorage.setItem('traceloop_user', JSON.stringify(freshMe));
      } catch (err) {
        console.error("Failed to refresh user session:", err);
      }
      // ------------------------------------------------------------------

      const tx = d.chain_tx_id || d.tx_id || '';
      $('#complete-result').innerHTML = `<div class="notice success">
        Device recycled on-chain. Earned eco points!
        ${tx ? `<a href="https://lora.algokit.io/testnet/transaction/${encodeURIComponent(tx)}" target="_blank">View on Lora ↗</a>` : ''}
        ${d.certificate_url ? `<br><a href="${esc(d.certificate_url)}" target="_blank">Download CPCB certificate ↗</a>` : ''}
      </div>`;
      
      toast('Device recycled. Eco points credited.');
      e.target.reset();
      loadRecyclerIncoming();
      
      // Reload the page to display the freshly saved points from localStorage
      setTimeout(() => location.reload(), 1500);

    } catch (x) {
      $('#complete-result').innerHTML = `<div class="notice error">${esc(x.message)}</div>`;
      toast(x.message, 'error');
    } finally { 
      setBusy(e.submitter, false); 
    }
  });

  $('#refresh-incoming')?.addEventListener('click', loadRecyclerIncoming);
  loadRecyclerIncoming();
}

async function loadRecyclerIncoming() {
  const el = $('#recycler-incoming');
  if (!el) return;
  try {
    const d = envelope(await api('/transfers/incoming'));
    const list = d.incoming_transfers || d || [];
    el.innerHTML = list.length
      ? list.map(x => `
        <div class="transfer-row">
          <div>
            <span class="mono">${esc(x.device_id)}</span>
            <h3>${esc(x.brand_name || 'Device')}</h3>
            <p class="muted" style="font-size:.82rem">From ${esc(x.from_user_id)} · ${fmtDate(x.initiated_at)}</p>
          </div>
          <div class="row-actions">${chip(x.status)}</div>
        </div>`).join('')
      : empty('No incoming transfers', 'Devices transferred to your facility appear here.');
  } catch (e) { el.innerHTML = empty('Error', e.message); }
}


/* ── ADMIN: Verifier oversight with dispute count ────────────── */
async function loadAdminVerifiers() {
  const el = $('#verifiers-list');
  if (!el) return;
  try {
    const d = envelope(await api('/admin/verifiers'));
    const list = d.verifiers || (Array.isArray(d) ? d : []);
    el.innerHTML = list.length
      ? list.map(x => {
          const disputes = x.dispute_count || 0;
          const flagged  = disputes > 1;
          return `
          <div class="transfer-row">
            <div>
              <strong>${esc(x.name || '—')}</strong>
              <p class="muted" style="font-size:.82rem">${esc(x.phone || '—')} · ${esc(x.brand_auth_code || '—')}</p>
              <div style="margin-top:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <span style="font-size:.78rem;background:${flagged ? 'var(--red-bg)' : 'var(--green-bg)'};
                  color:${flagged ? 'var(--red)' : 'var(--green)'};
                  border-radius:20px;padding:2px 10px;font-weight:600;">
                  ${disputes} disputed stamp${disputes !== 1 ? 's' : ''}
                  ${flagged ? ' ⚠ AUTO-FLAGGED' : ''}
                </span>
                ${chip(x.status || 'ACTIVE')}
              </div>
            </div>
            <div class="row-actions">
              ${x.status === 'ACTIVE'
                ? `<button class="button danger small" data-revoke="${esc(x.user_id || x.id)}">Revoke</button>`
                : '<span class="muted" style="font-size:.82rem">Revoked</span>'
              }
            </div>
          </div>`;
        }).join('')
      : empty('No verifiers', 'No authorized service centers onboarded yet.');

    $$('[data-revoke]', el).forEach(b => b.addEventListener('click', async () => {
      if (!confirm(`Revoke this verifier? They will lose stamp authority immediately.`)) return;
      setBusy(b, true, 'Revoking…');
      try {
        await api(`/admin/verifiers/${b.dataset.revoke}/revoke`, { method: 'POST' });
        toast('Verifier authorization revoked.');
        loadAdminVerifiers();
      } catch (e) { toast(e.message, 'error'); }
      finally { setBusy(b, false); }
    }));
  } catch (e) { el.innerHTML = empty('Error loading verifiers', e.message); }
}

/* ═══════════════════════════════════════════════════════════════
   INIT ROUTER
══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  renderShell();
  const p = document.body.dataset.page;
  if (p === 'landing')         initLanding();
  if (p === 'login')           initLogin();
  if (p === 'register')        initRegister();
  if (p === 'dashboard')       loadDashboard();
  if (p === 'track')           initTrack();
  if (p === 'transfer')        initTransfer();
  if (p === 'device-register') initRegisterDevice();
  if (p === 'recycling')       initRecycling();
});

window.logout       = logout;
window.searchDevice = searchDevice;
/* ── USER NOTIFICATIONS & ALERTS ─────────────────────────────── */
/* ── USER NOTIFICATIONS & ALERTS ─────────────────────────────── */
async function loadUserNotifications() {
  const root = $('#dashboard-content');
  if (!root) return;

  try {
    const me = user();
    const isRecycler = me?.role === 'RECYCLER';

    // 1. Fetch KYC docs and role-appropriate item list simultaneously
    const promises = [api('/api/v1/kyc/my-documents')];
    if (isRecycler) {
      promises.push(api('/devices/my-devices')); // Recyclers check owned inventory
    } else {
      promises.push(api('/transfers/incoming')); // Buyers/First Buyers check pending handoffs
    }

    const [kycRes, secondaryRes] = await Promise.allSettled(promises);

    const docs = kycRes.status === 'fulfilled' ? envelope(kycRes.value).documents || [] : [];
    const secondaryData = secondaryRes.status === 'fulfilled' ? envelope(secondaryRes.value) : {};
    
    const itemsList = isRecycler 
      ? (secondaryData.devices || secondaryData || []) 
      : (secondaryData.incoming_transfers || secondaryData || []);

    if (!docs.length && (!Array.isArray(itemsList) || !itemsList.length)) return;

    const dismissed = JSON.parse(localStorage.getItem('traceloop_dismissed_notifs') || '[]');
    let alertHtml = '<div class="stack" id="notif-stack" style="margin-bottom:32px; display:flex; flex-direction:column; gap:12px;">';
    let hasVisible = false;

    // Beautiful UI Wrapper for Alerts
    const buildNotice = (id, type, icon, title, desc, actionHtml = '') => {
      const closeBtn = `<button class="close-notif" data-id="${id}" style="position:absolute; top:12px; right:12px; background:none; border:none; font-size:1.4rem; cursor:pointer; opacity:0.4; transition:opacity 0.2s; line-height:1; padding:4px;">&times;</button>`;
      return `
        <div class="notice ${type}" style="position:relative; display:flex; gap:16px; align-items:flex-start; padding:16px 20px; border-radius:var(--radius); overflow:hidden;">
          <div style="font-size:1.6rem; line-height:1; margin-top:2px;">${icon}</div>
          <div style="flex:1; padding-right:24px;">
            <strong style="display:block; font-size:1.05rem; margin-bottom:4px; font-family:'Space Grotesk',sans-serif;">${title}</strong>
            <span style="display:block; font-size:0.9rem; line-height:1.5; opacity:0.9;">${desc}</span>
            ${actionHtml ? `<div style="margin-top:12px;">${actionHtml}</div>` : ''}
          </div>
          ${closeBtn}
        </div>`;
    };

    // 2. Render KYC Notifications
    docs.forEach(doc => {
      if (dismissed.includes(doc.doc_id)) return;
      hasVisible = true;

      if (doc.status === 'PENDING') {
        alertHtml += buildNotice(
          doc.doc_id, 'info', '⏳', 
          'Document Review Pending', 
          `Your upload for ${doc.serial_for_device ? `<span class="mono">${esc(doc.serial_for_device)}</span>` : 'account verification'} is currently being reviewed by the admin team.`
        );
      } else if (doc.status === 'ACCEPTED' && doc.serial_for_device) {
        alertHtml += buildNotice(
          doc.doc_id, 'success', '✅', 
          'Purchase Proof Approved', 
          `Your invoice for <span class="mono">${esc(doc.serial_for_device)}</span> was verified. You can now mint this device's passport on-chain.`,
          `<a class="button primary small" href="register-device.html?serial=${encodeURIComponent(doc.serial_for_device)}">Register Device Now</a>`
        );
      } else if (doc.status === 'REJECTED') {
        alertHtml += buildNotice(
          doc.doc_id, 'error', '❌', 
          'Document Rejected', 
          `Your upload for ${doc.serial_for_device ? `<span class="mono">${esc(doc.serial_for_device)}</span>` : 'verification'} was rejected.<br><strong style="margin-top:4px;display:block;">Reason:</strong> ${esc(doc.rejection_reason || 'Invalid document.')}`
        );
      }
    });

    // 3. Render Role-Specific Notifications (Recycler Inventory vs Incoming Handoffs)
    if (Array.isArray(itemsList)) {
      itemsList.forEach(item => {
        const itemId = item.transfer_id || item.device_id;
        if (dismissed.includes(itemId)) return;

        if (isRecycler) {
          // For Recycler: Notify when a completed device is ready for recycling processing
          hasVisible = true;
          alertHtml += buildNotice(
            itemId, 'success', '♻️', 
            'Device Ready for Recycling', 
            `Device <span class="mono">${esc(item.device_id)}</span> has been transferred to your facility and is ready for final recycling completion.`,
            `<a class="button primary small" href="recycling.html">Open Recycling Desk</a>`
          );
        } else {
          // For Buyers & First Buyers: Render incoming transfer notifications
          if (item.status === 'PENDING') {
            hasVisible = true;
            alertHtml += buildNotice(
              itemId, 'warning', '📦', 
              'Incoming Transfer Required', 
              `You have a pending device handoff for <span class="mono">${esc(item.device_id)}</span> from User ${esc((item.from_user_id || '').slice(0,8))}...`,
              `<button class="button primary small" onclick="document.getElementById('incoming')?.scrollIntoView({behavior:'smooth'})">View Inbox</button>`
            );
          } else if (item.status === 'VERIFIED' || item.status === 'ACCEPTED') {
            hasVisible = true;
            alertHtml += buildNotice(
              itemId, 'success', '🟢', 
              'Device Verified & Ready', 
              `Device <span class="mono">${esc(item.device_id)}</span> has been verified on-chain.`,
              `<a class="button primary small" href="dashboard.html">View Dashboard</a>`
            );
          }
        }
      });
    }

    alertHtml += '</div>';

    if (hasVisible) {
      root.insertAdjacentHTML('afterbegin', alertHtml);

      // Smooth CSS Collapse Animation for Dismissal
      $$('.close-notif').forEach(btn => {
        btn.addEventListener('mouseenter', e => e.currentTarget.style.opacity = '1');
        btn.addEventListener('mouseleave', e => e.currentTarget.style.opacity = '0.4');
        btn.addEventListener('click', (e) => {
          const id = e.currentTarget.dataset.id;
          dismissed.push(id);
          localStorage.setItem('traceloop_dismissed_notifs', JSON.stringify(dismissed));
          
          const noticeDiv = e.currentTarget.closest('.notice');
          const height = noticeDiv.offsetHeight;
          
          noticeDiv.style.height = height + 'px';
          noticeDiv.style.transition = 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
          
          requestAnimationFrame(() => {
            noticeDiv.style.opacity = '0';
            noticeDiv.style.transform = 'scale(0.98)';
            noticeDiv.style.height = '0px';
            noticeDiv.style.paddingTop = '0px';
            noticeDiv.style.paddingBottom = '0px';
            noticeDiv.style.marginTop = '0px';
            noticeDiv.style.marginBottom = '0px';
            noticeDiv.style.borderWidth = '0px';
          });
          
          setTimeout(() => noticeDiv.remove(), 300);
        });
      });
    }
    
  } catch (e) {
    console.error("Could not load notifications:", e);
  }
}