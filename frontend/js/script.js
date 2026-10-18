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

const fmtDate = v => {
  if (!v) return '—';
  
  // 1. Sanitize malformed +00:00Z strings from Python
  let clean = String(v).trim();
  if (clean.includes('+') && clean.endsWith('Z')) {
    clean = clean.slice(0, -1);
  }

  let d;
  // 2. Parse Unix numeric timestamp vs ISO string
  if (!isNaN(clean) && Number(clean) > 10000) {
    const ms = Number(clean) < 1e12 ? Number(clean) * 1000 : Number(clean);
    d = new Date(ms);
  } else {
    d = new Date(clean);
  }

  return (d instanceof Date && !isNaN(d.getTime()))
    ? d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
    : '—';
};
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
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData;

  const buildHeaders = (accessToken = token()) => {
    const out = {};
    if (!isFormData) out['Content-Type'] = 'application/json';

    for (const [key, value] of Object.entries(options.headers || {})) {
      if (isFormData && key.toLowerCase() === 'content-type') continue;
      out[key] = value;
    }

    // The browser must set multipart/form-data with its generated boundary.
    if (isFormData) {
      for (const key of Object.keys(out)) {
        if (key.toLowerCase() === 'content-type') delete out[key];
      }
    }

    if (accessToken && options.auth !== false) out.Authorization = `Bearer ${accessToken}`;
    else {
      for (const key of Object.keys(out)) {
        if (key.toLowerCase() === 'authorization') delete out[key];
      }
    }
    return out;
  };

  let headers = buildHeaders();
  let r = await fetch(`${API_BASE}${path}`, { ...options, headers });

  // Auto-refresh on 401, preserving JSON vs multipart headers on the retry.
  if (r.status === 401 && options.auth !== false && localStorage.getItem(REFRESH_KEY)) {
    const rr = await fetch(`${API_BASE}/api/v1/token/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: localStorage.getItem(REFRESH_KEY) })
    });
    if (rr.ok) {
      const fresh = envelope(await rr.json());
      if (fresh.access_token) {
        localStorage.setItem(TOKEN_KEY, fresh.access_token);
        if (fresh.refresh_token) localStorage.setItem(REFRESH_KEY, fresh.refresh_token);
        headers = buildHeaders(fresh.access_token);
        r = await fetch(`${API_BASE}${path}`, { ...options, headers });
      }
    }
  }

  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401) logout(false);
    throw new Error(errorMsg(body));
  }
  return body;
}

function absoluteApiUrl(path) {
  try {
    return new URL(path, `${API_BASE.replace(/\/+$/, '')}/`).toString();
  } catch {
    return `${API_BASE.replace(/\/+$/, '')}/${String(path || '').replace(/^\/+/, '')}`;
  }
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


const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

function appendIfValue(formData, key, value) {
  if (value !== null && value !== undefined && String(value).trim() !== '') {
    formData.append(key, String(value).trim());
  }
}

async function uploadEvidence({ docType, file, serial, claimedDocumentNumber, claimedIssuer, issuedAt, expiresAt }) {
  if (!(file instanceof File)) throw new Error('Please select a document file.');
  if (file.size <= 0) throw new Error('The selected file is empty.');
  if (file.size > MAX_UPLOAD_BYTES) throw new Error('The maximum document size is 5 MB.');

  const form = new FormData();
  form.append('doc_type', docType);
  form.append('file', file, file.name);
  appendIfValue(form, 'serial_for_device', serial);
  appendIfValue(form, 'claimed_document_number', claimedDocumentNumber);
  appendIfValue(form, 'claimed_issuer', claimedIssuer);
  appendIfValue(form, 'issued_at', issuedAt);
  appendIfValue(form, 'expires_at', expiresAt);

  // The server computes the authoritative SHA-256 from the uploaded bytes.
  return envelope(await api('/api/v1/evidence/upload', { method: 'POST', body: form }));
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

// Maps role → KYC document metadata.
// Certificate expiry collection is deliberately not enabled in this phase; add it back only
// with the complete, agreed backend/admin expiry policy.
const KYC_META = {
  FIRST_BUYER: {
    doc_type: 'PURCHASE_PROOF',
    label: 'Purchase proof',
    needSerial: true,
    optional: false,
    captureDocumentMetadata: true,
    notice: `
      <div style="background:var(--amber-bg);border:1px solid var(--amber);border-radius:var(--radius);padding:14px 16px;font-size:.875rem;color:#92400e;margin-bottom:20px">
        <strong style="display:block;margin-bottom:8px">📄 What to upload</strong>
        GST invoice, warranty registration email, or authorized dealer receipt for the laptop.
        <ul style="margin:8px 0 0 16px">
          <li>The document must clearly show the laptop's <strong>serial number</strong>.</li>
          <li>Enter that exact serial number in the field below.</li>
          <li>The admin must approve proof matched to this exact serial before device registration.</li>
        </ul>
      </div>`,
    activation: 'Admin reviews your purchase proof and serial. Device registration unlocks after approval.',
  },
  BUYER: {
    doc_type: 'IDENTITY_PROOF',
    label: 'Identity proof',
    needSerial: false,
    optional: false,
    captureDocumentMetadata: false,
    notice: `<div class="notice info" style="margin-bottom:20px">Upload an identity document for admin review. Do not type an Aadhaar or other government ID number into TraceLoop; only upload the document needed for review.</div>`,
    activation: 'Your account remains pending until an admin reviews and accepts your identity proof.',
  },
  RESELLER: {
    doc_type: 'BUSINESS_REGISTRATION',
    label: 'Business proof',
    needSerial: false,
    optional: false,
    captureDocumentMetadata: true,
    notice: `
      <div style="background:var(--amber-bg);border:1px solid var(--amber);border-radius:var(--radius);padding:14px 16px;font-size:.875rem;color:#92400e;margin-bottom:20px">
        <strong style="display:block;margin-bottom:8px">📄 What to upload</strong>
        Trade license, GST registration certificate, or shop-establishment proof. The business credential belongs to your account, not to an individual device.
      </div>`,
    activation: 'Admin reviews your business registration proof. The account activates after acceptance.',
  },
  VERIFIABLE: {
    doc_type: 'BRAND_AUTHORIZATION',
    label: 'Brand authorization letter',
    needSerial: false,
    optional: false,
    captureDocumentMetadata: true,
    notice: `<div class="notice info" style="margin-bottom:20px">Upload a manufacturer/OEM authorization letter or service-centre authorization. The admin will inspect the actual file before accepting it.</div>`,
    activation: 'Admin reviews your brand/service-centre authorization. The account activates after acceptance.',
  },
  RECYCLER: {
    doc_type: 'RECYCLER_AUTHORIZATION',
    label: 'CPCB / SPCB recycler authorization',
    needSerial: false,
    optional: false,
    captureDocumentMetadata: true,
    notice: `<div class="notice info" style="margin-bottom:20px">Upload your CPCB/SPCB e-waste recycler authorization certificate. The admin will inspect the actual file before accepting it. Certificate time-bound rules will be added in a later aligned change.</div>`,
    activation: 'Admin reviews the recycler authorization. The account activates after acceptance.',
  },
};

const ROLE_ACTIVATION = {
  FIRST_BUYER: 'Admin reviews your purchase proof and activates your account.',
  BUYER:       'Admin reviews your identity proof before activating your account.',
  RESELLER:    'Admin reviews your business registration proof. Account activates after acceptance.',
  VERIFIABLE:  'Admin reviews the brand/service-centre authorization. Account activates after acceptance.',
  RECYCLER:    'Admin reviews the recycler authorization. Account activates after acceptance.',
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
  const dropzone = $('#kyc-dropzone');
  const submitBtn = $('#btn-kyc-submit');

  fileInput?.addEventListener('change', async () => {
    const f = fileInput.files?.[0];
    if (!f) {
      submitBtn.disabled = true;
      dropzone?.classList.remove('has-file');
      if ($('#hash-preview')) $('#hash-preview').style.display = 'none';
      return;
    }
    if (f.size <= 0 || f.size > MAX_UPLOAD_BYTES) {
      fileInput.value = '';
      submitBtn.disabled = true;
      dropzone?.classList.remove('has-file');
      toast(f.size > MAX_UPLOAD_BYTES ? 'The maximum document size is 5 MB.' : 'The selected file is empty.', 'error');
      return;
    }

    dropzone?.classList.add('has-file');
    $('#dz-filename').textContent = `${f.name} (${(f.size / 1024).toFixed(1)} KB)`;
    submitBtn.disabled = true;
    const preview = $('#hash-preview');
    if (preview) {
      preview.style.display = 'block';
      preview.querySelector('strong')?.replaceChildren(document.createTextNode('Local SHA-256 preview (server recomputes the authoritative hash)'));
      $('#hash-value').textContent = 'Computing…';
    }

    try {
      const digest = await hashFile(f);
      $('#hash-value').textContent = digest;
      submitBtn.disabled = false;
    } catch (err) {
      console.error('Local SHA-256 preview failed:', err);
      $('#hash-value').textContent = 'Preview unavailable; the server will verify uploaded bytes.';
      // Local preview is not an authorization control. The server validates the actual file.
      submitBtn.disabled = false;
    }
  });

  dropzone?.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone?.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone?.addEventListener('drop', e => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer?.files?.length && fileInput) {
      fileInput.files = e.dataTransfer.files;
      fileInput.dispatchEvent(new Event('change'));
    }
  });

  // ── Step 3 → actual multipart evidence upload ────────────────
  $('#kyc-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const file = fileInput?.files?.[0];
    const serial = normalizeClientSerial($('#kyc-serial')?.value);
    const docType = $('#kyc-doc-type')?.value;
    const role = selectedRole || user()?.role;
    const meta = KYC_META[role];

    if (!file) { toast('Please select a document to upload.', 'error'); return; }
    if (meta?.needSerial && !serial) { toast('Serial number is required and must match the invoice.', 'error'); return; }

    setBusy(e.submitter || submitBtn, true, 'Uploading actual document…');
    try {
      const d = await uploadEvidence({
        docType,
        file,
        serial,
        claimedDocumentNumber: $('#kyc-claimed-number')?.value,
        claimedIssuer: $('#kyc-claimed-issuer')?.value,
        issuedAt: $('#kyc-issued-at')?.value,
        expiresAt: $('#kyc-expires-at')?.value,
      });

      const result = $('#kyc-result');
      result.innerHTML = `<div class="notice success">
        <strong>Document uploaded for admin review.</strong><br>
        Evidence ID: <span class="mono">${esc(d.evidence_id || d.doc_id || '—')}</span><br>
        ${d.serial_for_device ? `Serial on file: <span class="mono">${esc(d.serial_for_device)}</span><br>` : ''}
        <small>The server calculated the authoritative SHA-256. An administrator must inspect the actual file before approving the evidence.</small>
      </div>`;
      $('#kyc-done').style.display = 'block';
      toast('Evidence uploaded and queued for review.');
    } catch (x) {
      $('#kyc-result').innerHTML = `<div class="notice error">${esc(x.message)}</div>`;
      toast(x.message, 'error');
    } finally {
      setBusy(e.submitter || submitBtn, false);
    }
  });
}

function normalizeClientSerial(value) {
  const cleaned = String(value || '').trim().toUpperCase().replace(/[^A-Z0-9-]/g, '');
  return cleaned || null;
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
  const meta = KYC_META[role] || KYC_META.BUYER;
  const typeInput = $('#kyc-doc-type');
  if (typeInput) typeInput.value = meta.doc_type;

  const box = $('#kyc-role-box');
  if (box) {
    const metadataFields = meta.captureDocumentMetadata ? `
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:16px 0 20px">
        <label>Document / certificate number <span class="muted">(optional)</span>
          <input id="kyc-claimed-number" maxlength="80" autocomplete="off" placeholder="As printed on document">
        </label>
        <label>Issuing authority <span class="muted">(optional)</span>
          <input id="kyc-claimed-issuer" maxlength="120" autocomplete="off" placeholder="e.g. CPCB, SPCB, OEM">
        </label>
        <label>Issue date <span class="muted">(optional)</span>
          <input id="kyc-issued-at" type="date">
        </label>
        ${meta.needsExpiry ? `<label>Certificate expiry date <span class="req">*</span>
          <input id="kyc-expires-at" type="date" required>
          <small class="muted">This is your claimed date. The admin must confirm it from the inspected certificate.</small>
        </label>` : ''}
      </div>` : (meta.needsExpiry ? `<label>Certificate expiry date <span class="req">*</span><input id="kyc-expires-at" type="date" required></label>` : '');

    box.innerHTML = `
      <div style="margin-bottom:12px">
        <strong style="font-family:'Space Grotesk',sans-serif">${esc(meta.label)}</strong>
        <p class="muted" style="font-size:.82rem;margin-top:2px">${esc(role)} account · ${esc(meta.activation || '')}</p>
      </div>
      ${meta.notice || ''}
      ${metadataFields}
    `;
  }

  const serialWrap = $('#kyc-serial-wrap');
  if (serialWrap) serialWrap.style.display = meta.needSerial ? 'block' : 'none';
  const serialInput = $('#kyc-serial');
  if (serialInput) {
    serialInput.required = !!meta.needSerial;
    if (!meta.needSerial) serialInput.value = '';
  }

  // Every applicable role must submit its configured evidence; there is no buyer skip path.
  $('#kyc-skip-button')?.remove();
  const stepHeading = $('#step-3 h2');
  if (stepHeading) stepHeading.textContent = 'Submit KYC document';
}

/* ═══════════════════════════════════════════════════════════════
   DASHBOARD
═══════════════════════════════════════════════════════════════ */
function renderCredentialUploadPanel(root, me) {
  const uploadTypes = {
    FIRST_BUYER: { docType: 'PURCHASE_PROOF', label: 'Submit / resubmit purchase proof', requiresSerial: true },
    BUYER: { docType: 'IDENTITY_PROOF', label: 'Submit / resubmit identity proof', requiresSerial: false },
    RESELLER: { docType: 'BUSINESS_REGISTRATION', label: 'Renew / replace business registration', requiresSerial: false },
    VERIFIABLE: { docType: 'BRAND_AUTHORIZATION', label: 'Renew / replace brand authorization', requiresSerial: false },
    RECYCLER: { docType: 'RECYCLER_AUTHORIZATION', label: 'Renew / replace recycler authorization', requiresSerial: false },
  };
  const cfg = uploadTypes[me?.role];
  if (!cfg || !root) return;
  // Active first buyers use the device-registration flow for additional device invoices.
  // The dashboard upload panel is for KYC_IN_PROGRESS users resubmitting their first proof.
  if (['FIRST_BUYER', 'BUYER'].includes(me.role) && me.status === 'ACTIVE') return;

  const panel = document.createElement('section');
  panel.className = 'panel';
  panel.id = 'credential-renewal-panel';
  panel.innerHTML = `
    <div class="panel-head">
      <div><span class="eyebrow">Evidence management</span><h2>${esc(cfg.label)}</h2></div>
    </div>
    <p class="muted" style="margin-bottom:16px">Upload the required or corrected evidence here. An admin must review the actual file before it can satisfy the account's verification requirement.</p>
    <form id="credential-renewal-form" class="form-grid" enctype="multipart/form-data">
      ${cfg.requiresSerial ? `<label>Device serial number <span class="req">*</span><input name="serial_for_device" maxlength="80" required placeholder="Exactly as printed on the invoice"></label>` : ''}
      <label>Document / certificate number <span class="muted">(optional)</span>
        <input name="claimed_document_number" maxlength="80" autocomplete="off" placeholder="As printed on the document">
      </label>
      <label>Issuing authority <span class="muted">(optional)</span>
        <input name="claimed_issuer" maxlength="120" autocomplete="off" placeholder="e.g. CPCB, SPCB, OEM">
      </label>
      <label>Issue date <span class="muted">(optional)</span>
        <input name="issued_at" type="date">
      </label>
      <label style="grid-column:1/-1">Actual document file <span class="req">*</span>
        <input id="credential-renewal-file" name="file" type="file" accept=".pdf,.jpg,.jpeg,.png,.webp" required>
        <small class="muted">PDF, JPG, PNG or WebP · maximum 5 MB.</small>
      </label>
      <div style="grid-column:1/-1;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <button class="button primary" type="submit" id="credential-renewal-submit" disabled>Upload for admin review</button>
        <span id="credential-renewal-file-name" class="muted" aria-live="polite"></span>
      </div>
    </form>
    <div id="credential-renewal-result" style="margin-top:14px"></div>
  `;
  root.prepend(panel);

  const fileInput = $('#credential-renewal-file', panel);
  const submit = $('#credential-renewal-submit', panel);
  const fileName = $('#credential-renewal-file-name', panel);
  fileInput?.addEventListener('change', () => {
    const file = fileInput.files?.[0];
    const valid = !!file && file.size > 0 && file.size <= MAX_UPLOAD_BYTES;
    submit.disabled = !valid;
    fileName.textContent = file ? `${file.name} (${(file.size / 1024).toFixed(1)} KB)` : '';
    if (file && !valid) {
      fileInput.value = '';
      fileName.textContent = '';
      toast(file.size > MAX_UPLOAD_BYTES ? 'The maximum document size is 5 MB.' : 'The selected file is empty.', 'error');
    }
  });

  $('#credential-renewal-form', panel)?.addEventListener('submit', async e => {
    e.preventDefault();
    const form = e.currentTarget;
    const file = fileInput?.files?.[0];
    const values = new FormData(form);
    if (!file) { toast('Select the actual document file.', 'error'); return; }
    if (cfg.requiresSerial && !normalizeClientSerial(values.get('serial_for_device'))) { toast('Enter the device serial number exactly as shown on the purchase proof.', 'error'); return; }

    setBusy(e.submitter || submit, true, 'Uploading…');
    try {
      const d = await uploadEvidence({
        docType: cfg.docType,
        file,
        serial: normalizeClientSerial(values.get('serial_for_device')),
        claimedDocumentNumber: values.get('claimed_document_number'),
        claimedIssuer: values.get('claimed_issuer'),
        issuedAt: values.get('issued_at'),
        expiresAt: values.get('expires_at'),
      });
      $('#credential-renewal-result', panel).innerHTML = `<div class="notice success">
        <strong>Evidence submitted for review.</strong><br>
        Evidence ID: <span class="mono">${esc(d.evidence_id || d.doc_id || '—')}</span><br>
        Status: ${chip(d.status || 'PENDING')}<br>
      </div>`;
      form.reset();
      submit.disabled = true;
      fileName.textContent = '';
      toast('Evidence uploaded for admin review.');
      try {
        const freshMe = envelope(await api('/api/v1/me'));
        localStorage.setItem(USER_KEY, JSON.stringify(freshMe));
        $('#account-status').innerHTML = chip(freshMe.status);
      } catch { /* Upload success is already confirmed; a later profile refresh can retry. */ }
      loadUserNotifications();
    } catch (err) {
      $('#credential-renewal-result', panel).innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
      toast(err.message, 'error');
    } finally {
      setBusy(e.submitter || submit, false);
      if (!fileInput.files?.length) submit.disabled = true;
    }
  });
}

async function loadDashboard() {
  if (!requireAuth()) return;

  // Refresh the profile so the backend's current account-review state is reflected in the UI.
  let me;
  try {
    me = envelope(await api('/api/v1/me'));
    localStorage.setItem(USER_KEY, JSON.stringify(me));
  } catch (err) {
    const cached = user();
    if (!cached) { logout(); return; }
    me = cached;
    toast('Could not refresh account status. Protected backend operations still enforce authorization.', 'warning');
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
    if (me.role === 'FIRST_BUYER')
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
            <h3 style="margin-bottom:16px">Report a dispute</h3>
            <form id="dispute-form" class="form-grid">
              <label>Device ID <span class="req">*</span>
              <input name="device_id" required placeholder="e.g. TL-DELL-A1B2C3D4E5F60718293A4B5C"
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
              <input name="device_id" required placeholder="e.g. TL-DELL-A1B2C3D4E5F60718293A4B5C"
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
                <div class="panel" id="analytics">
          <div class="panel-head">
            <div>
              <span class="eyebrow">Regulator view</span>
              <h2>Lifecycle analytics</h2>
            </div>
            <button class="button ghost small" id="refresh-analytics">Refresh</button>
          </div>

          <div id="analytics-grid" class="admin-grid">
            <div class="admin-card">
              <span class="eyebrow">Total devices</span>
              <span class="metric" id="analytics-total">—</span>
              <p class="muted">Registered devices</p>
            </div>

            <div class="admin-card">
              <span class="eyebrow">Registered</span>
              <span class="metric" id="analytics-registered">—</span>
              <p class="muted">Current lifecycle state</p>
            </div>

            <div class="admin-card">
              <span class="eyebrow">Verified</span>
              <span class="metric" id="analytics-verified">—</span>
              <p class="muted">Currently verified</p>
            </div>

            <div class="admin-card">
              <span class="eyebrow">Transferred</span>
              <span class="metric" id="analytics-transferred">—</span>
              <p class="muted">Ownership transitions</p>
            </div>

            <div class="admin-card">
              <span class="eyebrow">Disputed</span>
              <span class="metric" id="analytics-disputed">—</span>
              <p class="muted">Devices under dispute</p>
            </div>

            <div class="admin-card">
              <span class="eyebrow">Recycled</span>
              <span class="metric" id="analytics-recycled">—</span>
              <p class="muted">Terminal recycled devices</p>
            </div>

            <div class="admin-card">
              <span class="eyebrow">Exported</span>
              <span class="metric" id="analytics-exported">—</span>
              <p class="muted">Terminal exported devices</p>
            </div>

            <div class="admin-card">
              <span class="eyebrow">Hardware changes</span>
              <span class="metric" id="analytics-hardware">—</span>
              <p class="muted">Recorded configuration changes</p>
            </div>

            <div class="admin-card">
              <span class="eyebrow">Recycling rate</span>
              <span class="metric" id="analytics-rate">—</span>
              <p class="muted">Recycled ÷ total devices</p>
            </div>
          </div>

          <div style="margin-top:24px">
            <div class="section-label">Total On-chain events</div>
            <div id="analytics-events" class="stack" style="margin-top:10px">
              <div class="loading">Loading analytics…</div>
            </div>
          </div>
        </div>

        <div class="panel" id="disputes">
          <div class="panel-head"><div><span class="eyebrow">Open disputes</span><h2>Dispute queue</h2></div></div>
          <div id="disputes-list" class="stack"><div class="loading">Loading…</div></div>
        </div>
        <div class="panel" id="approvals">
          <div class="panel-head">
            <div><span class="eyebrow">Account activation</span><h2>Pending account approvals</h2></div>
          </div>
          <p class="muted" style="margin:0 0 14px">This is the same onboarding request created from the user's account. Review the required credential in the KYC queue first; account activation becomes available only after that credential is verified.</p>
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

        
      `;
    }
  } // <--- END OF ELSE BLOCK


  root.innerHTML = html || empty('No workspace', 'Your role does not have a configured dashboard.');
  renderCredentialUploadPanel(root, me);
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
      ? list.map(x => {
          const stampValid = !!x.stamp_valid;
          const legacyTest = x.is_legacy_test === true
            || x.identity_version === 'V1_LEGACY'
            || x.registration_status === 'LEGACY_TEST';
          const confirmedV2 = x.identity_version === 'V2_PUBLIC_ID'
            && x.registration_status === 'CONFIRMED'
            && x.read_only !== true;
          const readOnly = !confirmedV2;
          const canViewPassport = legacyTest || confirmedV2;
          const terminal = ['RECYCLED', 'EXPORTED'].includes(
            String(x.status || '').toUpperCase()
          );

          return `
            <article class="device-card">
              <div class="device-top">
                <span class="mono">${esc(x.device_id)}</span>
                ${chip(legacyTest ? 'V1 LEGACY' : (x.status === 'TRANSFERRED' ? 'NEWLY ACQUIRED' : x.status))}
              </div>

              <h3 style="margin:8px 0 6px">
                ${esc(x.brand_name || x.brand || 'Laptop')}
              </h3>

              <p class="muted" style="font-size:.82rem">
                ${esc(
                  Object.entries(x.current_config || {})
                    .slice(0, 3)
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(' · ') || 'Config on file'
                )}
              </p>

              ${legacyTest ? `
                <div class="notice warning" style="margin-top:10px;padding:10px 12px;font-size:.78rem">
                  V1 Legacy Test Passport — read-only historical record. Its synthetic display ID was not registered on-chain.
                </div>
              ` : (readOnly ? `
                <div class="notice warning" style="margin-top:10px;padding:10px 12px;font-size:.78rem">
                  Registration is not confirmed. Lifecycle actions stay disabled until the V2 registration is reconciled.
                </div>
              ` : '')}

              ${confirmedV2 ? `
                <p class="muted" style="font-size:.78rem;margin-top:4px">
                  Stamp:
                  ${
                    stampValid
                      ? '<span style="color:var(--green)">✓ Valid</span>'
                      : '<span style="color:var(--red)">✗ Invalid — re-verify first</span>'
                  }
                </p>
              ` : ''}

              ${
                confirmedV2 && !stampValid && !terminal
                  ? `
                    <div class="notice warning"
                         style="margin-top:10px;padding:10px 12px;font-size:.78rem">
                      Hardware/configuration was changed or verification is no longer
                      valid. Re-verification is required before resale or transfer.
                    </div>
                  `
                  : ''
              }

              <div class="card-actions">

                ${canViewPassport ? `
                  <a class="button ghost small"
                     href="track.html?id=${encodeURIComponent(x.device_id)}">
                    View passport
                  </a>
                ` : ''}

                ${confirmedV2 ? `
                <button
                  class="button secondary small"
                  data-hardware-change="${esc(x.device_id)}"
                  ${
                    terminal
                      ? 'disabled title="Terminal devices cannot be modified"'
                      : ''
                  }>
                  Declare hardware change
                </button>

                ${
                  x.is_for_sale
                    ? `
                      <button
                        class="button danger small"
                        data-unlist="${esc(x.device_id)}">
                        Unlist
                      </button>
                    `
                    : `
                      <button
                        class="button secondary small"
                        data-list="${esc(x.device_id)}"
                        ${
                          !stampValid || terminal
                            ? 'disabled title="A valid verification stamp is required before listing"'
                            : ''
                        }>
                        List for sale
                      </button>
                    `
                }

                <button
                  class="button ghost small"
                  data-transfer="${esc(x.device_id)}"
                  ${
                    !stampValid || terminal
                      ? 'disabled title="A valid verification stamp is required before transfer"'
                      : ''
                  }>
                  Transfer
                </button>
                ` : `<span class="muted" style="font-size:.8rem">${legacyTest ? 'V1 Legacy — read-only' : 'Lifecycle actions unavailable until registration is confirmed'}</span>`}


              </div>
            </article>
          `;
        }).join('')
      : empty(
          'No devices yet',
          'Register a laptop or receive a verified transfer.'
        );

    $$('[data-list]', el).forEach(b =>
      b.addEventListener('click', () =>
        openListModal(b.dataset.list)
      )
    );

    $$('[data-unlist]', el).forEach(b =>
      b.addEventListener('click', () =>
        unlistDevice(b.dataset.unlist, b)
      )
    );

    $$('[data-transfer]', el).forEach(b =>
      b.addEventListener('click', () =>
        initiateTransfer(b.dataset.transfer, b)
      )
    );

    $$('[data-hardware-change]', el).forEach(b =>
      b.addEventListener('click', () =>
        openHardwareChangeModal(b.dataset.hardwareChange)
      )
    );

  } catch (e) {
    el.innerHTML = empty(
      'Unable to load devices',
      e.message
    );
  }
}

/* ── Hardware Delta Engine: owner-declared hardware changes ─────── */

function ensureHardwareChangeModal() {
  if ($('#hardware-change-modal')) return;

  document.body.insertAdjacentHTML('beforeend', `
    <div id="hardware-change-modal"
         style="
           display:none;
           position:fixed;
           inset:0;
           background:rgba(13,31,23,.72);
           z-index:600;
           place-items:center;
           padding:20px;
         ">

      <div class="card"
           style="
             max-width:620px;
             width:100%;
             max-height:90vh;
             overflow:auto;
           "
           onclick="event.stopPropagation()">

        <div style="
          display:flex;
          justify-content:space-between;
          align-items:flex-start;
          gap:16px;
        ">

          <div>
            <span class="eyebrow">Hardware Delta Engine</span>

            <h2 style="margin:4px 0 6px;">
              Declare hardware change
            </h2>

            <p class="muted" style="font-size:.86rem;">
              Record a component change against this device's existing passport.
            </p>
          </div>

          <button
            class="button ghost small"
            type="button"
            id="close-hardware-change-modal">
            Close
          </button>

        </div>

        <div
          id="hardware-current-config"
          class="notice info"
          style="margin:18px 0;padding:12px 14px;">
          Loading current configuration…
        </div>

        <form id="hardware-change-form" class="form-grid">

          <input
            type="hidden"
            id="hardware-device-id"
            name="device_id">

          <label>
            Component <span class="req">*</span>

            <input
              id="hardware-component"
              name="component"
              required
              maxlength="80"
              placeholder="RAM, Storage, Battery, SSD…">
          </label>

          <label>
            Change type <span class="req">*</span>

            <select
              id="hardware-change-type"
              name="change_type"
              required>

              <option value="">Select change</option>
              <option value="UPGRADED">Upgraded</option>
              <option value="REPLACED">Replaced</option>
              <option value="ADDED">Added</option>
              <option value="REMOVED">Removed</option>

            </select>
          </label>

          <label>
            Previous specification

            <input
              id="hardware-old-spec"
              name="old_spec"
              readonly
              placeholder="Loaded from current passport">
          </label>

          <label>
            New specification <span class="req">*</span>

            <input
              id="hardware-new-spec"
              name="new_spec"
              maxlength="200"
              placeholder="e.g. 16GB DDR4"
              required>
          </label>

          <div
            class="notice warning"
            style="margin-top:4px;padding:12px 14px;">

            <strong>Important:</strong>
            Recording a hardware change invalidates the current verification stamp.

            <br><br>

            The device must be physically re-verified before it can be listed
            or transferred again.

          </div>

          <div style="
            display:flex;
            gap:10px;
            justify-content:flex-end;
          ">

            <button
              class="button ghost"
              type="button"
              id="cancel-hardware-change">
              Cancel
            </button>

            <button
              class="button primary"
              type="submit">
              Record hardware change
            </button>

          </div>

        </form>

        <div
          id="hardware-change-result"
          style="margin-top:16px;">
        </div>

        <div style="margin-top:24px;">

          <div class="eyebrow">
            Hardware change history
          </div>

          <div
            id="hardware-log-list"
            class="stack"
            style="margin-top:10px;">

            <div class="loading">
              No device selected.
            </div>

          </div>

        </div>

      </div>
    </div>
  `);

  const modal = $('#hardware-change-modal');

  $('#close-hardware-change-modal')
    ?.addEventListener('click', closeHardwareChangeModal);

  $('#cancel-hardware-change')
    ?.addEventListener('click', closeHardwareChangeModal);

  modal?.addEventListener('click', e => {
    if (e.target === modal) {
      closeHardwareChangeModal();
    }
  });

  $('#hardware-change-form')
    ?.addEventListener('submit', submitHardwareChange);

  $('#hardware-component')
    ?.addEventListener('input', e => {
      e.target.value = e.target.value.trimStart();
      refreshHardwareOldSpec();
    });

  $('#hardware-change-type')
    ?.addEventListener('change', () => {
      refreshHardwareOldSpec();

      const type = $('#hardware-change-type')?.value;
      const newSpec = $('#hardware-new-spec');

      if (!newSpec) return;

      if (type === 'REMOVED') {
        newSpec.value = 'REMOVED';
        newSpec.disabled = true;
      } else {
        if (newSpec.value === 'REMOVED') {
          newSpec.value = '';
        }

        newSpec.disabled = false;
        newSpec.placeholder =
          type === 'ADDED'
            ? 'e.g. Secondary SSD 1TB'
            : 'e.g. 16GB DDR4';
      }
    });
}


function closeHardwareChangeModal() {
  const modal = $('#hardware-change-modal');

  if (modal) {
    modal.style.display = 'none';
  }
}


function configValueForComponent(config, component) {
  if (!config || !component) return null;

  const key = Object.keys(config).find(
    k =>
      String(k).toLowerCase() ===
      String(component).trim().toLowerCase()
  );

  return key ? config[key] : null;
}


async function openHardwareChangeModal(deviceId) {
  ensureHardwareChangeModal();

  $('#hardware-device-id').value = deviceId;

  $('#hardware-component').value = '';
  $('#hardware-change-type').value = '';
  $('#hardware-old-spec').value = '';
  $('#hardware-new-spec').value = '';
  $('#hardware-new-spec').disabled = false;

  $('#hardware-change-result').innerHTML = '';

  $('#hardware-current-config').innerHTML =
    'Loading current configuration…';

  $('#hardware-log-list').innerHTML =
    '<div class="loading">Loading hardware history…</div>';

  const modal = $('#hardware-change-modal');

  if (modal) {
    modal.style.display = 'grid';
  }

  try {

    const deviceData = envelope(
      await api(`/devices/${encodeURIComponent(deviceId)}`)
    );

    const device = deviceData.device || deviceData;

    const config = device.current_config || {};
    const entries = Object.entries(config);

    $('#hardware-current-config').innerHTML = `
      <strong>Current passport configuration</strong>

      <div style="
        margin-top:8px;
        font-family:'Space Mono',monospace;
        font-size:.78rem;
        line-height:1.7;
      ">
        ${
          entries.length
            ? entries
                .map(
                  ([k, v]) =>
                    `${esc(k)}: ${esc(v)}`
                )
                .join('<br>')
            : 'No configuration recorded.'
        }
      </div>
    `;

    $('#hardware-change-modal').dataset.currentConfig =
      JSON.stringify(config);

    await loadHardwareLog(deviceId);

    refreshHardwareOldSpec();

  } catch (e) {

    $('#hardware-current-config').innerHTML =
      `<span style="color:var(--red)">
        Unable to load device configuration:
        ${esc(e.message)}
      </span>`;

    $('#hardware-log-list').innerHTML =
      empty(
        'Hardware history unavailable',
        e.message
      );
  }
}


function refreshHardwareOldSpec() {
  const component =
    $('#hardware-component')?.value.trim();

  const type =
    $('#hardware-change-type')?.value;

  const modal =
    $('#hardware-change-modal');

  if (!modal) return;

  let config = {};

  try {
    config = JSON.parse(
      modal.dataset.currentConfig || '{}'
    );
  } catch {
    config = {};
  }

  const current =
    configValueForComponent(
      config,
      component
    );

  const oldSpec =
    $('#hardware-old-spec');

  if (!oldSpec) return;

  oldSpec.value =
    current == null
      ? ''
      : String(current);

  if (type === 'ADDED' && current != null) {

    oldSpec.placeholder =
      'Component already exists — use Upgraded/Replaced';

  } else if (type !== 'ADDED' && current == null) {

    oldSpec.placeholder =
      'Component not currently recorded';

  } else {

    oldSpec.placeholder =
      'Loaded from current passport';

  }
}


async function loadHardwareLog(deviceId) {
  const el = $('#hardware-log-list');

  if (!el) return;

  try {

    const d = envelope(
      await api(
        `/device-profiles/${encodeURIComponent(deviceId)}/hardware-log`
      )
    );

    const logs =
      d.hardware_log || [];

    if (!logs.length) {

      el.innerHTML =
        empty(
          'No hardware changes recorded',
          'Future component changes will appear here as append-only audit entries.'
        );

      return;
    }

    el.innerHTML =
      logs
        .map(
          log => `
            <div class="transfer-row">

              <div>

                <span class="mono">
                  ${esc(log.component)}
                </span>

                <h3 style="margin:4px 0;">
                  ${esc(log.change_type || 'CHANGE')}
                </h3>

                <p
                  class="muted"
                  style="font-size:.82rem;margin:0;">

                  ${esc(log.old_spec ?? '—')}
                  →
                  ${esc(log.new_spec ?? '—')}

                </p>

                <p
                  class="muted"
                  style="font-size:.75rem;margin:4px 0 0;">

                  Declared
                  ${fmtDate(log.declared_at)}

                </p>

              </div>

              <div class="row-actions">

                <span class="chip chip-error">
                  STAMP INVALIDATED
                </span>

              </div>

            </div>
          `
        )
        .join('');

  } catch (e) {

    el.innerHTML =
      empty(
        'History unavailable',
        e.message
      );
  }
}


async function submitHardwareChange(e) {
  e.preventDefault();

  const form =
    e.currentTarget;

  const submitButton =
    e.submitter ||
    $('button[type="submit"]', form);

  const deviceId =
    $('#hardware-device-id')
      ?.value
      .trim();

  const component =
    $('#hardware-component')
      ?.value
      .trim();

  const changeType =
    $('#hardware-change-type')
      ?.value;

  let newSpec =
    $('#hardware-new-spec')
      ?.value
      .trim();

  const oldSpec =
    $('#hardware-old-spec')
      ?.value || null;

  if (
    !deviceId ||
    !component ||
    !changeType
  ) {
    toast(
      'Complete the component and change type.',
      'error'
    );
    return;
  }

  // REMOVED uses a deterministic value because the backend schema
  // still expects new_spec.
  if (changeType === 'REMOVED') {
    newSpec = 'REMOVED';
  }

  if (!newSpec) {
    toast(
      'Enter the new specification.',
      'error'
    );
    return;
  }

  if (
    changeType === 'ADDED' &&
    oldSpec
  ) {
    toast(
      'That component already exists. Choose Upgraded or Replaced.',
      'error'
    );
    return;
  }

  if (
    changeType !== 'ADDED' &&
    changeType !== 'REMOVED' &&
    !oldSpec
  ) {
    toast(
      'That component is not present in the current configuration. Choose Added.',
      'error'
    );
    return;
  }

  if (
    changeType === 'REMOVED' &&
    !oldSpec
  ) {
    toast(
      'That component is not present in the current configuration.',
      'error'
    );
    return;
  }

  setBusy(
    submitButton,
    true,
    'Writing change…'
  );

  try {

    const d = envelope(
      await api(
        `/device-profiles/${encodeURIComponent(deviceId)}/hardware-change`,
        {
          method: 'POST',

          body: JSON.stringify({
            component,
            change_type: changeType,

            // Sent only for compatibility/display.
            // Updated backend derives the authoritative
            // old value directly from the database.
            old_spec: oldSpec,

            new_spec: newSpec
          })
        }
      )
    );

    const tx =
      d.chain_tx_id ||
      d.tx_id ||
      '';

    $('#hardware-change-result').innerHTML = `
      <div class="notice success">

        <strong>
          Hardware change recorded.
        </strong>

        <br>

        ${esc(oldSpec ?? '—')}
        →
        ${esc(d.new_spec || newSpec)}

        <br>

        <span>
          Current verification stamp:
          <strong>INVALID</strong>
        </span>

        ${
          tx
            ? `
              <br>
              <small>
                Algorand TX:

                <a
                  href="https://lora.algokit.io/testnet/transaction/${encodeURIComponent(tx)}"
                  target="_blank"
                  rel="noopener">

                  ${esc(tx.slice(0, 16))}… ↗

                </a>
              </small>
            `
            : ''
        }

      </div>
    `;

    toast(
      'Hardware change recorded. Re-verification is now required.'
    );

    // Refresh authoritative state.
    await loadMyDevices();

    // Refresh audit history.
    await loadHardwareLog(deviceId);

    // Reset form fields after successful submission.
    $('#hardware-component').value = '';
    $('#hardware-change-type').value = '';
    $('#hardware-old-spec').value = '';
    $('#hardware-new-spec').value = '';
    $('#hardware-new-spec').disabled = false;

  } catch (x) {

    $('#hardware-change-result').innerHTML =
      `<div class="notice error">
        ${esc(x.message)}
       </div>`;

    toast(
      x.message,
      'error'
    );

  } finally {

    setBusy(
      submitButton,
      false
    );
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
            ${chip(x.is_legacy_test ? 'LEGACY_TEST' : x.status)}
            ${x.read_only
              ? `<span class="muted" style="font-size:.8rem">${x.is_legacy_test ? 'V1 Legacy — read-only' : 'Actions unavailable until V2 registration is confirmed'}</span>`
              : x.status === 'PENDING'
                ? `<button class="button primary small" data-accept="${esc(x.transfer_id)}">Accept</button>
                   <button class="button ghost small" data-cancel="${esc(x.transfer_id)}">Cancel</button>`
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
    const list = d.my_disputes || d.disputes || d || [];
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
      await api('/disputes/report', {
        method: 'POST',
        body: JSON.stringify(Object.fromEntries(fd.entries()))
      });
      toast('Dispute report submitted. An administrator will review it before the device is frozen on-chain.');
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
            ${chip(x.is_legacy_test ? 'LEGACY_TEST' : (x.stamp_valid ? 'VERIFIED' : 'NEEDS STAMP'))}
            ${x.read_only
              ? `<span class="muted" style="font-size:.8rem">${x.is_legacy_test ? 'V1 Legacy — read-only' : 'Actions unavailable until V2 registration is confirmed'}</span>`
              : `<button class="button primary small" data-complete="${esc(x.transfer_id)}">Complete transfer</button>`
            }
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
  await Promise.all([
    loadAdminAnalytics(),
    loadAdminDisputes(),
    loadAdminApprovals(),
    loadAdminKyc(),
    loadAdminDevices()
  ]);

  $('#refresh-analytics')?.addEventListener(
    'click',
    loadAdminAnalytics
  );
}
async function loadAdminAnalytics() {
  const grid = $('#analytics-grid');
  if (!grid) return;

  try {
    const d = envelope(await api('/analytics/overview'));

    /* ── API sections ───────────────────────────────────────── */
    const summary =
      d.summary || {};

    const lifecycle =
      d.lifecycle_distribution || {};

    const hardware =
      d.hardware_activity || {};

    const disputes =
      d.dispute_statistics || {};

    const recycling =
      d.recycling_activity || {};

    const onChain =
      d.on_chain_events || {};

    /* ── Number formatter ───────────────────────────────────── */
    const numberValue = value => {
      if (
        value === null ||
        value === undefined ||
        value === ''
      ) {
        return '—';
      }

      const n = Number(value);

      return Number.isFinite(n)
        ? n.toLocaleString('en-IN')
        : esc(value);
    };

    /* ── Recycling rate ─────────────────────────────────────── */
    const rawRate =
      summary.recycling_rate_percent ??
      recycling.formal_recycling_rate_percent ??
      null;

    let rateText = '—';

    if (
      rawRate !== null &&
      rawRate !== undefined &&
      rawRate !== ''
    ) {
      const numericRate = Number(rawRate);

      if (Number.isFinite(numericRate)) {
        rateText = `${numericRate.toFixed(1)}%`;
      }
    }

    /* ── Lifecycle analytics cards ──────────────────────────── */

    $('#analytics-total').textContent =
      numberValue(
        summary.total_devices_registered
      );

    $('#analytics-registered').textContent =
      numberValue(
        lifecycle.REGISTERED
      );

    $('#analytics-verified').textContent =
      numberValue(
        lifecycle.VERIFIED
      );

    $('#analytics-transferred').textContent =
      numberValue(
        lifecycle.TRANSFERRED
      );

    $('#analytics-disputed').textContent =
      numberValue(
        lifecycle.DISPUTED
      );

    $('#analytics-recycled').textContent =
      numberValue(
        lifecycle.RECYCLED
      );

    $('#analytics-exported').textContent =
      numberValue(
        lifecycle.EXPORTED
      );

    $('#analytics-hardware').textContent =
      numberValue(
        hardware.total_hardware_changes_declared
      );

    $('#analytics-rate').textContent =
      rateText;

    /* ── On-chain event totals ──────────────────────────────── */

    const eventsEl = $('#analytics-events');

    if (!eventsEl) return;

    const eventEntries =
      Object.entries(onChain);

    if (!eventEntries.length) {
      eventsEl.innerHTML = empty(
        'No on-chain events',
        'No lifecycle event totals were returned by the analytics service.'
      );

      return;
    }

    eventsEl.innerHTML =
      eventEntries
        .map(([eventType, count]) => `
          <div class="transfer-row">
            <div>
              <span class="mono">
                ${esc(eventType)}
              </span>

              <h3 style="margin:4px 0">
                ${esc(
                  String(eventType)
                    .replace(/_/g, ' ')
                )}
              </h3>

              <p
                class="muted"
                style="font-size:.78rem">
                Immutable Algorand lifecycle events recorded
              </p>
            </div>

            <div class="row-actions">
              <span class="chip chip-verified">
                ${numberValue(count)}
              </span>
            </div>
          </div>
        `)
        .join('');

  } catch (e) {

    $('#analytics-total').textContent = '—';

    $('#analytics-registered').textContent = '—';

    $('#analytics-verified').textContent = '—';

    $('#analytics-transferred').textContent = '—';

    $('#analytics-disputed').textContent = '—';

    $('#analytics-recycled').textContent = '—';

    $('#analytics-exported').textContent = '—';

    $('#analytics-hardware').textContent = '—';

    $('#analytics-rate').textContent = '—';

    const eventsEl =
      $('#analytics-events');

    if (eventsEl) {
      eventsEl.innerHTML = empty(
        'Analytics unavailable',
        e.message
      );
    }
  }
}

async function loadAdminDisputes() {
  const el = $('#disputes-list');
  if (!el) return;

  try {
    const d = envelope(await api('/disputes/open'));
    const list = d.disputes || d || [];

    $('#cnt-disputes').textContent =
      Array.isArray(list) ? list.length : (d.total ?? '—');

    el.innerHTML = list.length
      ? list.map(x => `
        <div class="transfer-row">
          <div>
            <span class="mono">${esc(x.device_id)}</span>
            <h3>${esc(String(x.dispute_type || '').replace(/_/g, ' '))}</h3>
            <p class="muted" style="font-size:.82rem">
              ${esc(x.description || '')}
              · Raised by ${esc(x.raised_by || '—')}
            </p>
          </div>

          <div class="row-actions">
            ${chip(x.status)}
            ${x.is_legacy_test ? '<span class="muted" style="font-size:.8rem">Legacy test · read-only</span>' : ''}

            ${
              !x.is_legacy_test && x.status === 'UNDER_REVIEW'
                ? `
                  <button
                    class="button danger small"
                    data-freeze="${esc(x.dispute_id || x.id)}">
                    Freeze on-chain
                  </button>
                `
                : ''
            }

            ${
              !x.is_legacy_test && x.status === 'OPEN'
                ? `
                  <button
                    class="button primary small"
                    data-resolve="${esc(x.dispute_id || x.id)}">
                    Resolve
                  </button>
                `
                : ''
            }
          </div>
        </div>
      `).join('')
      : empty(
          'No active disputes',
          'There are currently no disputes awaiting review or resolution.'
        );

    $$('[data-freeze]', el).forEach(b =>
      b.addEventListener('click', () =>
        adminFreezeDispute(b.dataset.freeze, b)
      )
    );

    $$('[data-resolve]', el).forEach(b =>
      b.addEventListener('click', () =>
        adminResolveDispute(b.dataset.resolve)
      )
    );

  } catch (e) {
    el.innerHTML = empty('Error', e.message);
  }
}
async function adminFreezeDispute(id, btn) {
  if (!confirm(
    'Freeze this device on Algorand? This will write the DISPUTED state to the blockchain.'
  )) {
    return;
  }

  setBusy(btn, true, 'Freezing…');

  try {
    const d = envelope(
      await api(`/disputes/${encodeURIComponent(id)}/freeze`, {
        method: 'POST'
      })
    );

    const tx = d.chain_tx_id || d.tx_id || '';

    toast(
      tx
        ? `Device frozen on-chain. TX: ${tx.slice(0, 16)}…`
        : 'Device frozen on-chain.'
    );

    await loadAdminDisputes();

  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setBusy(btn, false);
  }
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

/* ── ACCOUNT ACTIVATION REQUESTS ─────────────────────────────── */
async function loadAdminApprovals() {
  const el = $('#approvals-list');
  if (!el) return;
  try {
    const data = envelope(await api('/admin/approvals/pending'));
    const list = Array.isArray(data.pending_accounts) ? data.pending_accounts : [];
    const count = $('#cnt-approvals');
    if (count) count.textContent = list.length;

    const stageLabels = {
      DOCUMENT_REQUIRED: 'Document required',
      WAITING_FOR_DOCUMENT_REVIEW: 'Waiting for document review',
      READY_FOR_ACCOUNT_APPROVAL: 'Ready for account approval',
      CORRECTION_REQUIRED: 'Corrected document required',
      CREDENTIAL_EXPIRED: 'Credential expired',
      CREDENTIAL_REVOKED: 'Credential revoked',
      CREDENTIAL_UNAVAILABLE: 'Credential unavailable'
    };

    el.innerHTML = list.length ? list.map(account => {
      const id = account.user_id || account.id;
      const credentialVerified = account.credential_status === 'VERIFIED';
      const stage = stageLabels[account.activation_stage] || 'Awaiting verification';
      return `
        <article class="transfer-row account-approval-card">
          <div style="flex:1;min-width:240px">
            <strong>${esc(account.name || '—')}</strong>
            <p class="muted" style="font-size:.82rem">${esc(account.role || '—')} · ${esc(account.phone || '—')}</p>
            ${account.brand_auth_code ? `<p class="muted" style="font-size:.78rem">Brand authorization: ${esc(account.brand_auth_code)} · Service centre: ${esc(account.service_center_id || '—')}</p>` : ''}
            ${account.cpcb_number ? `<p class="muted" style="font-size:.78rem">CPCB reference: ${esc(account.cpcb_number)}</p>` : ''}
            <p style="font-size:.82rem;margin-top:7px">Required credential: ${chip(account.credential_status || 'MISSING')}</p>
            <p class="muted" style="font-size:.82rem">Activation request: <strong>${esc(stage)}</strong></p>
            ${account.credential_evidence_id ? `<p class="muted" style="font-size:.75rem">Evidence ID: <span class="mono">${esc(account.credential_evidence_id)}</span></p>` : ''}
          </div>
          <div class="row-actions">
            <button class="button primary small" data-approve-account="${esc(id)}" ${credentialVerified ? '' : 'disabled title="The required credential must be verified in the KYC queue before account activation"'}>Approve account</button>
            <button class="button danger small" data-reject-account="${esc(id)}">Reject onboarding</button>
          </div>
        </article>`;
    }).join('') : empty('No pending account approvals', 'Accounts appear here after phone verification. Their activation button becomes available when the required document has been accepted.');

    $$('[data-approve-account]', el).forEach(button => button.addEventListener('click', () => adminAccountDecision(button.dataset.approveAccount, 'approve', button)));
    $$('[data-reject-account]', el).forEach(button => button.addEventListener('click', () => adminAccountDecision(button.dataset.rejectAccount, 'reject', button)));
  } catch (error) {
    el.innerHTML = empty('Unable to load account approvals', error.message);
  }
}

async function adminAccountDecision(userId, decision, button) {
  let notes = 'Admin approved via dashboard';
  if (decision === 'reject') {
    notes = prompt('Reason for rejecting this onboarding request (required):');
    if (!notes || !notes.trim()) {
      toast('A rejection reason is required.', 'error');
      return;
    }
  }
  setBusy(button, true, 'Working…');
  try {
    const result = envelope(await api(`/admin/approvals/${encodeURIComponent(userId)}/${decision}`, {
      method: 'POST',
      body: JSON.stringify({ notes: notes.trim ? notes.trim() : notes })
    }));
    toast(result.message || (decision === 'approve' ? 'Account activated.' : 'Onboarding rejected.'));
    await loadAdminApprovals();
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    setBusy(button, false);
  }
}

/* ── PRIVATE EVIDENCE VIEWER (JWT only; no bearer token in URLs) ── */
async function fetchProtectedEvidenceBlob(path) {
  const requestWithToken = accessToken => fetch(`${API_BASE}${path}`, {
    method: 'GET',
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {}
  });

  let accessToken = token();
  if (!accessToken) throw new Error('Please sign in as an administrator to inspect evidence.');
  let response = await requestWithToken(accessToken);

  // Keep binary viewing compatible with the normal API helper's access-token refresh behavior.
  if (response.status === 401 && localStorage.getItem(REFRESH_KEY)) {
    const refreshResponse = await fetch(`${API_BASE}/api/v1/token/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: localStorage.getItem(REFRESH_KEY) })
    });
    if (refreshResponse.ok) {
      const fresh = envelope(await refreshResponse.json().catch(() => ({})));
      if (fresh.access_token) {
        localStorage.setItem(TOKEN_KEY, fresh.access_token);
        if (fresh.refresh_token) localStorage.setItem(REFRESH_KEY, fresh.refresh_token);
        accessToken = fresh.access_token;
        response = await requestWithToken(accessToken);
      }
    }
  }

  if (!response.ok) {
    const body = await response.clone().json().catch(() => ({}));
    throw new Error(body.detail || body.message || `Unable to inspect evidence (HTTP ${response.status}).`);
  }
  return response.blob();
}

async function openProtectedEvidence(evidenceId, button) {
  // Open the tab synchronously from the user click so popup blockers do not swallow it.
  const previewWindow = window.open('about:blank', '_blank');
  if (!previewWindow) {
    toast('Your browser blocked the document tab. Allow pop-ups for TraceLoop and retry.', 'error');
    return;
  }
  try { previewWindow.opener = null; } catch (_) {}

  setBusy(button, true, 'Opening…');
  let objectUrl = null;
  try {
    const blob = await fetchProtectedEvidenceBlob(`/admin/evidence/${encodeURIComponent(evidenceId)}/view`);
    objectUrl = URL.createObjectURL(blob);
    previewWindow.location.replace(objectUrl);
    // The document has been downloaded into this same-origin Blob URL; revoke it later.
    window.setTimeout(() => { if (objectUrl) URL.revokeObjectURL(objectUrl); }, 5 * 60 * 1000);
  } catch (error) {
    try { previewWindow.close(); } catch (_) {}
    toast(error.message || 'Could not open the protected document.', 'error');
  } finally {
    setBusy(button, false);
  }
}

/* ── ADMIN KYC MANAGEMENT ────────────────────────────────────── */
async function loadAdminKyc() {
  const el = $('#kyc-list');
  if (!el) return;
  try {
    const d = envelope(await api('/admin/kyc/pending'));
    const list = Array.isArray(d.pending_documents) ? d.pending_documents : [];
    if ($('#cnt-kyc')) $('#cnt-kyc').textContent = list.length;

    el.innerHTML = list.length ? list.map(x => {
      const reviewId = x.doc_id || x.evidence_id || x.id;
      const evidenceId = x.evidence_id || (String(reviewId || '').startsWith('EV-') ? reviewId : null);
      const viewPath = x.protected_view_url || x.view_url;
      const canApprove = !!x.can_approve && !!x.has_stored_file && !!evidenceId && !!viewPath && !x.is_legacy_hash_only;
      return `
        <article class="transfer-row evidence-review-card" data-review-id="${esc(reviewId)}" data-evidence-id="${esc(evidenceId || '')}" data-requires-expiry="${x.requires_expiry ? 'true' : 'false'}">
          <div style="flex:1;min-width:240px">
            <strong>${esc(x.doc_type || 'Document')}</strong>
            <p class="muted" style="font-size:.82rem">${esc(x.user_name || 'Unknown user')} · ${esc(x.user_role || '—')} · ${esc(x.user_phone || x.user_id || '—')}</p>
            <p class="muted" style="font-size:.78rem">Uploaded ${fmtDate(x.uploaded_at)} · Subject: ${esc(x.subject_type || '—')}</p>
            ${x.serial_for_device ? `<p class="muted" style="font-size:.78rem">Device serial: <span class="mono">${esc(x.serial_for_device)}</span></p>` : ''}
            ${x.claimed_document_number ? `<p class="muted" style="font-size:.78rem">Claimed document number: ${esc(x.claimed_document_number)}</p>` : ''}
            ${x.claimed_issuer ? `<p class="muted" style="font-size:.78rem">Claimed issuer: ${esc(x.claimed_issuer)}</p>` : ''}
            ${x.issued_at ? `<p class="muted" style="font-size:.78rem">Issue date: ${esc(x.issued_at)}</p>` : ''}
            ${x.file_hash ? `<p class="muted" style="font-size:.75rem;font-family:'Space Mono',monospace;margin-top:4px">SHA-256: <span style="color:var(--green)">${esc(String(x.file_hash).slice(0, 24))}…</span></p>` : ''}
            <div style="margin-top:10px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
              ${viewPath && x.has_stored_file && evidenceId ? `<button type="button" class="button secondary small" data-view-evidence="${esc(evidenceId)}">Inspect document ↗</button>` : '<span class="chip chip-error">No inspectable file</span>'}
              ${x.is_legacy_hash_only ? '<span class="muted" style="font-size:.8rem">Legacy hash-only record — re-upload actual file before accepting.</span>' : ''}
            </div>
            ${!x.is_legacy_hash_only && x.has_stored_file ? `
              <label style="display:flex;align-items:flex-start;gap:8px;margin-top:12px;font-size:.84rem">
                <input type="checkbox" data-inspected style="margin-top:3px">
                <span>I opened and inspected the actual document and checked that its details match the claim.</span>
              </label>
              ${x.requires_expiry ? `<label style="display:block;margin-top:10px;font-size:.84rem">Expiry date confirmed from certificate <span class="req">*</span>
                <input type="date" data-confirmed-expiry required style="display:block;margin-top:5px;max-width:260px">
              </label>` : ''}
              <label style="display:block;margin-top:10px;font-size:.84rem">Reviewer notes <span class="muted">(optional)</span>
                <input type="text" data-review-notes maxlength="3000" placeholder="What was checked?" style="display:block;margin-top:5px;width:100%;max-width:520px">
              </label>
            ` : ''}
          </div>
          <div class="row-actions" style="align-self:flex-start">
            ${chip(x.status || 'PENDING')}
            <button class="button primary small" data-kyc-approve="${esc(reviewId)}" ${canApprove ? '' : 'disabled title="A linked, pending evidence record and stored file are required"'}>Approve</button>
            <button class="button danger small" data-kyc-reject="${esc(reviewId)}" ${x.status && x.status !== 'PENDING' ? 'disabled' : ''}>Reject</button>
          </div>
        </article>`;
    }).join('') : empty('KYC queue is clear', 'No evidence records are pending review.');

    $$('[data-view-evidence]', el).forEach(b => b.addEventListener('click', () => openProtectedEvidence(b.dataset.viewEvidence, b)));
    $$('[data-kyc-approve]', el).forEach(b => b.addEventListener('click', () => adminKycReview(b.dataset.kycApprove, 'approve', b)));
    $$('[data-kyc-reject]', el).forEach(b => b.addEventListener('click', () => adminKycReview(b.dataset.kycReject, 'reject', b)));
  } catch (e) {
    el.innerHTML = empty('Unable to load evidence queue', e.message);
  }
}

async function adminKycReview(docId, action, btn) {
  const card = btn?.closest('.evidence-review-card');
  const payload = { decision: action === 'approve' ? 'ACCEPT' : 'REJECT' };

  if (action === 'reject') {
    const note = prompt('Rejection reason (required):');
    if (!note?.trim()) { toast('A rejection reason is required.', 'error'); return; }
    payload.rejection_reason = note.trim();
  } else {
    const inspected = card?.querySelector('[data-inspected]');
    if (!inspected?.checked) {
      toast('Open the protected viewer, inspect the actual file, then confirm the checkbox.', 'error');
      return;
    }
    payload.document_inspected = true;
    const expiryInput = card?.querySelector('[data-confirmed-expiry]');
    if (card?.dataset.requiresExpiry === 'true') {
      if (!expiryInput?.value) { toast('Enter the expiry date you confirmed on the certificate.', 'error'); expiryInput?.focus(); return; }
      payload.confirmed_expires_at = expiryInput.value;
    }
    const reviewNotes = card?.querySelector('[data-review-notes]')?.value?.trim();
    if (reviewNotes) payload.review_notes = reviewNotes;
  }

  setBusy(btn, true, action === 'approve' ? 'Verifying…' : 'Rejecting…');
  try {
    const reviewResult = envelope(await api(`/admin/kyc/${encodeURIComponent(docId)}/review`, {
      method: 'POST',
      body: JSON.stringify(payload)
    }));
    toast(reviewResult?.message || (action === 'approve' ? 'Evidence verified.' : 'Evidence rejected. Corrected evidence may be submitted while the account remains pending.'));
    await Promise.all([loadAdminKyc(), loadAdminApprovals()]);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    setBusy(btn, false);
  }
}

async function loadAdminDevices() {
  const el = $('#devices-list');
  if (!el) return;
  try {
    const d = envelope(await api('/admin/devices'));
    const list = d.devices || d || [];
    $('#cnt-devices').textContent = d.total ?? (Array.isArray(list) ? list.length : '—');
    el.innerHTML = list.length
      ? list.map(x => {
        const publicId = x.public_id || x.device_id;
        const legacy = x.is_legacy_test === true || x.identity_version === 'V1_LEGACY' || x.registration_status === 'LEGACY_TEST';
        const canViewPassport = legacy || (x.identity_version === 'V2_PUBLIC_ID' && x.registration_status === 'CONFIRMED');
        return `
        <div class="transfer-row">
          <div>
            <span class="mono">${esc(publicId)}</span>
            <h3>${esc(x.brand_name || x.brand || 'Device')}</h3>
            <p class="muted" style="font-size:.82rem">Owner: ${esc(x.current_owner_id || '—')} · ${fmtDate(x.registered_at)}</p>
          </div>
          <div class="row-actions">
            ${chip(legacy ? 'V1 LEGACY' : x.status)}
            ${canViewPassport ? `<a class="button ghost small" href="track.html?id=${encodeURIComponent(publicId)}">Passport</a>` : ''}
            ${!legacy && x.identity_version === 'V2_PUBLIC_ID' && x.registration_status === 'CONFIRMED' && !['RECYCLED', 'EXPORTED'].includes(x.status)
              ? `<button class="button danger small" data-export="${esc(publicId)}">Export</button>`
              : ''}
          </div>
        </div>`;
      }).join('')
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

  inputEl.value = inputEl.value.trim().toUpperCase().replace(/[^A-Z0-9-]/g, '');
  const id = inputEl.value;
  if (!id) { toast('Enter a device ID.', 'error'); return; }

  const result = $('#track-result');
  result.innerHTML = `<div class="loading">Loading passport for <span class="mono">${esc(id)}</span>…</div>`;

  try {
    // Public tracking uses one explicitly sanitized endpoint; it does not call
    // private owner, dispute, or authenticated ledger endpoints in parallel.
    const response = await api(`/devices/public/${encodeURIComponent(id)}`, { auth: false });
    const device = envelope(response);
    const history = Array.isArray(device.history) ? device.history : [];
    const events = Array.isArray(device.events) ? device.events : [];
    const isLegacy = !!device.is_legacy_test || device.identity_version === 'V1_LEGACY';

    result.innerHTML = `
      <div class="passport-header">
        <div>
          <span class="eyebrow">Digital Product Passport</span>
          <h2>${esc(device.brand_name || device.brand || 'Device passport')}</h2>
          <p class="mono" style="margin-top:4px">${esc(device.public_id || device.device_id || id)}</p>
        </div>
        ${device.status ? chip(device.status) : ''}
      </div>

      ${isLegacy ? `
        <div class="notice warning" style="margin:14px 0;padding:12px 14px">
          <strong>V1 Legacy Test Passport — read-only.</strong>
          This is a preserved historical test record. Its displayed public ID is an off-chain lookup ID; available historical chain state and mirrored lifecycle events are shown without enabling new lifecycle actions.
        </div>
      ` : ''}

      ${device.message && !isLegacy ? `<p class="notice info" style="margin:14px 0;padding:12px 14px">${esc(device.message)}</p>` : ''}

      <section class="passport-intelligence panel" style="margin:18px 0;padding:18px">
        <div class="panel-head">
          <div>
            <span class="eyebrow">Deterministic review</span>
            <h3>Explain and reconcile this passport</h3>
            <p class="muted" style="margin-top:5px">Compares the latest chain state, independently indexed lifecycle events, and the local record. It does not verify physical events by itself.</p>
          </div>
          <button class="button primary" type="button" id="passport-intelligence-button">Explain passport</button>
        </div>
        <div id="passport-intelligence-result" aria-live="polite">
          <p class="muted">Run an on-demand consistency check to see the plain-language history and any source differences.</p>
        </div>
      </section>

      <div class="stat-grid">
        <div><small>Database state</small><strong>${esc(device.status || '—')}</strong></div>
        <div><small>Chain state</small><strong>${esc(device.chain_status || '—')}</strong></div>
        <div><small>Stamp valid</small><strong style="color:${device.stamp_valid ? 'var(--green)' : 'var(--red)'}">
          ${device.stamp_valid ? '✓ Yes' : '✗ No'}
        </strong></div>
        <div><small>Registered</small><strong>${fmtDate(device.registered_at)}</strong></div>
      </div>

      <div class="passport-columns">
        <section>
          <div class="section-label">Configuration</div>
          <pre>${esc(JSON.stringify(device.current_config || {}, null, 2))}</pre>
        </section>
        <section>
          <div class="section-label">Ownership chain</div>
          <div class="mini-list">
            ${history.length
              ? history.map(item => `<div class="mini-row">
                  <strong>${esc(item.status || item.event_type || 'Record')}</strong>
                  <span>${fmtDate(item.timestamp)}</span>
                </div>`).join('')
              : '<p class="muted">No approved lifecycle history is available.</p>'
            }
          </div>
        </section>
      </div>

      <div class="ledger">
        <div class="section-label">Mirrored ledger events</div>
        <p class="muted" style="margin:4px 0 12px">These are the local database mirror. The deterministic review above checks independently indexed chain history as a separate source.</p>
        ${renderEvents(events)}
      </div>
    `;

    $('#passport-intelligence-button')?.addEventListener('click', () => loadPassportIntelligence(id));
  } catch (error) {
    result.innerHTML = empty('Passport unavailable', error.message || 'The passport could not be loaded.');
  }
}

async function loadPassportIntelligence(publicId) {
  const button = $('#passport-intelligence-button');
  const result = $('#passport-intelligence-result');
  if (!button || !result) return;

  setBusy(button, true, 'Checking sources and preparing explanation…');
  result.innerHTML = '<div class="loading">Reading chain state and comparing confirmed history…</div>';
  try {
    const response = await api(`/passport-intelligence/${encodeURIComponent(publicId)}`, { auth: false });
    result.innerHTML = renderPassportIntelligence(envelope(response));
  } catch (error) {
    result.innerHTML = `<p class="notice warning">${esc(error.message || 'The passport sources could not be compared right now.')}</p>`;
  } finally {
    setBusy(button, false);
  }
}

function renderPassportIntelligence(facts) {
  const reconciliation = facts?.reconciliation || {};
  const status = String(reconciliation.status || 'SOURCE_UNAVAILABLE').toUpperCase();
  const checks = Array.isArray(reconciliation.checks) ? reconciliation.checks : [];
  const timeline = Array.isArray(facts?.timeline) ? facts.timeline : [];
  const sources = facts?.sources || {};
  const limitations = Array.isArray(facts?.limitations) ? facts.limitations : [];
  const explanation = facts?.explanation || {};
  const explanationText = explanation.text || facts.summary || 'No explanation was returned.';

  // THE FIX: A mini-formatter to parse the AI's Markdown safely into UI elements
  const formatMarkdown = text => {
    if (!text) return '';
    let safe = esc(text);

    // Safety fallback: if backend collapsed newlines, inject them back before headers and bullets
    safe = safe.replace(/###/g, '\n###').replace(/ \*/g, '\n*');

    // Convert ### to styled H4 headers
    safe = safe.replace(/\n*### (.*?)(?:\n|$)/g, '<h4 style="margin: 20px 0 10px 0; font-size: 1.1rem; color: var(--text); display: flex; align-items: center; gap: 6px;">$1</h4>');

    // Convert **bold** to <strong>
    safe = safe.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

    // Convert * bullets to styled div rows
    safe = safe.replace(/\n\* (.*?)(?=\n|$)/g, '<div style="margin-left: 12px; padding: 4px 0; display: flex; align-items: flex-start; gap: 8px;"><span style="color: var(--muted)">•</span><span>$1</span></div>');

    // Convert remaining single newlines to <br> for paragraph spacing
    safe = safe.trim().replace(/\n/g, '<br>');
    return safe;
  };

  const checksHtml = checks.length ? `
    <div class="passport-checks">
      ${checks.map(check => `
        <div class="passport-check-row">
          <div><strong>${esc(String(check.field || 'Field').replaceAll('_', ' '))}</strong><p>${esc(check.explanation || '')}</p></div>
          <span class="passport-check-status ${esc(statusClass(check.result || 'not-comparable'))}">${esc(check.result || 'NOT_COMPARABLE')}</span>
        </div>
      `).join('')}
    </div>
  ` : '<p class="muted">No field-by-field comparison is available in this public view.</p>';

  const timelineHtml = timeline.length ? `
    <div class="passport-intelligence-timeline">
      ${timeline.map(event => `
        <div class="passport-intelligence-event">
          <div><strong>${esc(event.event_type || 'Lifecycle event')}</strong><p>${esc(event.description || '')}</p><small>${fmtDate(event.timestamp)}${event.round != null ? ` · round ${esc(event.round)}` : ''}</small></div>
          ${event.transaction_ref ? `<a href="https://lora.algokit.io/testnet/transaction/${encodeURIComponent(event.transaction_ref)}" target="_blank" rel="noopener">View transaction ↗</a>` : ''}
        </div>
      `).join('')}
    </div>
  ` : '<p class="muted">No public lifecycle event was returned for the scanned history window.</p>';

  const coverage = [
    ['Current chain state', sources.current_chain_state],
    ['Independent chain history', sources.independent_chain_history],
    ['Local event mirror', sources.database_event_mirror]
  ];

  const coverageHtml = `<div class="passport-source-grid">${coverage.map(([label, value]) => `
    <div><small>${esc(label)}</small><strong>${esc(value || 'UNKNOWN')}</strong></div>
  `).join('')}</div>`;

  const limitationsHtml = limitations.length ? `
    <div class="passport-limitations"><strong>Coverage notes</strong><ul>${limitations.map(note => `<li>${esc(note)}</li>`).join('')}</ul></div>
  ` : '';

  return `
    <div class="passport-intelligence-body">
      <div class="passport-reconciliation-head">
        <span class="passport-check-status ${esc(statusClass(status))}">${esc(status)}</span>
        <strong>${esc(reconciliation.headline || 'Reconciliation result unavailable.')}</strong>
      </div>

      <!-- AI Explanation injected with Markdown formatting -->
      <section class="passport-explanation">
        <div class="passport-explanation-label">Explanation</div>
        <div class="passport-summary" style="line-height: 1.6; font-size: 0.95rem;">
          ${formatMarkdown(explanationText)}
        </div>
      </section>

      ${checksHtml}
      <div class="section-label" style="margin-top:18px">Independent confirmed chain history</div>
      ${timelineHtml}
      <div class="section-label" style="margin-top:18px">Source coverage</div>
      ${coverageHtml}
      ${limitationsHtml}
      <p class="muted" style="font-size:.78rem;margin-top:12px">Checked ${fmtDate(facts.checked_at)}. The reconciliation badge and field checks are determined by fixed backend rules. A ledger event proves that the event was recorded, not that the physical event was independently verified.</p>
    </div>
  `;
}

function renderEvents(list) {
  if (!Array.isArray(list) || !list.length) return '<p class="muted">No ledger events returned.</p>';
  return `<div class="event-list">${list.map(x => {
    const tx = x.tx_hash || x.tx_id || x.transaction_id || '';
    
    // 🚨 THE FIX: Catch EVERY possible date key your backend uses across different database tables
    const rawTimestamp = 
      x.timestamp || 
      x['round-time'] ||     // Standard Algorand Indexer
      x.round_time || 
      x.initiated_at ||      // Used by Transfers
      x.registered_at ||     // Used by Devices
      x.created_at ||        // Used by Disputes
      x.uploaded_at ||       // Used by KYC
      x.last_verified_at ||  // Used by Verification
      x.verified_at || 
      x.updated_at ||
      x.date;

    return `<div class="event-row">
      <span class="event-dot"></span>
      <div>
        <strong>${esc(x.event_type || x.event || x.status || 'Event')}</strong>
        <p>${fmtDate(rawTimestamp)}${tx ? `· <a href="https://lora.algokit.io/testnet/transaction/${encodeURIComponent(tx)}"
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
    // Dynamically choose the route based on login status
    const path = token() ? '/transfers/marketplace/auth' : '/transfers/marketplace';
    const options = token() ? {} : { auth: false };
    
    // Call the API
    const d = envelope(await api(path, options));
    
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

const MARKETPLACE_BRAND_CODE_BY_LABEL = Object.freeze({
  DL: 'DL', DELL: 'DL',
  HP: 'HP',
  LN: 'LN', LENOVO: 'LN',
  AS: 'AS', ASUS: 'AS',
  AC: 'AC', ACER: 'AC',
  AP: 'AP', APPLE: 'AP',
  MS: 'MS', MSI: 'MS',
  SG: 'SG', SAMSUNG: 'SG',
});

function getMarketplaceListingBrandCode(listing) {
  // Current API payloads provide `brand` as the display name. Accept `brand_code`
  // and `brand_name` too so older/mixed API payloads remain filterable.
  for (const value of [listing?.brand_code, listing?.brand_name, listing?.brand]) {
    const key = String(value || '').trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
    if (Object.prototype.hasOwnProperty.call(MARKETPLACE_BRAND_CODE_BY_LABEL, key)) {
      return MARKETPLACE_BRAND_CODE_BY_LABEL[key];
    }
  }
  return '';
}

function applyFilters() {
  let list = [...allListings];
  const brand = $('#filter-brand')?.value;
  const sort  = $('#filter-sort')?.value;
  if (brand) list = list.filter(x => getMarketplaceListingBrandCode(x) === brand);
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

  // The public Passport ID is generated by the backend using a random suffix.
  // Never preview an ID derived from the hardware serial.
  const previewEl = $('#device-id-preview');
  if (previewEl) previewEl.textContent = 'Generated after registration';

  // Brand code → auto-fill canonical display name.
  const brandNames = { DL:'Dell', HP:'HP', LN:'Lenovo', AS:'Asus', AC:'Acer', AP:'Apple', MS:'MSI', SG:'Samsung' };
  $('#brand_code')?.addEventListener('change', e => {
    $('#brand_name').value = brandNames[e.target.value] || '';
  });

  // Smart auto-fill from notifications
  const urlParams = new URLSearchParams(window.location.search);
  const prefillSerial = urlParams.get('serial');
  if (prefillSerial && $('#serial_raw')) {
    $('#serial_raw').value = prefillSerial;
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
    const serial = normalizeClientSerial(fd.get('serial_raw'));

    if (!serial) { toast('Enter a valid device serial number.', 'error'); return; }

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
  $('#dz-filename').textContent =
    `${f.name} (${(f.size / 1024).toFixed(1)} KB)`;

  const btnToLock = submitKycBtn;

  btnToLock.disabled = true; 

  const preview = $('#hash-preview');

  if (preview) {
    preview.style.display = 'block';
    preview.querySelector('strong')?.replaceChildren(document.createTextNode('Local SHA-256 preview (server recomputes the authoritative hash)'));
    $('#hash-value').textContent = 'Computing…';
  }

  hashFile(f).then(h => {

    $('#hash-value').textContent = h;

    btnToLock.disabled = false;

  }).catch(err => {

    console.error('SHA-256 generation failed:', err);

    $('#hash-value').textContent = 'Preview unavailable; the server will verify uploaded bytes.';

    btnToLock.disabled = true;

    toast(
      'Could not generate the document hash.',
      'error'
    );
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

  // Submit actual invoice bytes through the new evidence upload endpoint.
  $('#new-kyc-form')?.addEventListener('submit', async e => {
    e.preventDefault();
    const file = fileInput?.files?.[0];
    const serialTarget = normalizeClientSerial($('#kyc-serial-display')?.textContent);
    if (!file) { toast('Please select an invoice to upload.', 'error'); return; }
    if (!serialTarget) { toast('The device serial could not be read; restart the device registration flow.', 'error'); return; }

    setBusy(e.submitter || submitKycBtn, true, 'Uploading invoice…');
    try {
      const d = await uploadEvidence({ docType: 'PURCHASE_PROOF', file, serial: serialTarget });
      $('#new-kyc-form').style.display = 'none';
      $('#new-kyc-result').innerHTML = `
        <div class="notice success">
          <strong>Invoice uploaded successfully.</strong><br>
          Admin must inspect and approve the invoice for <span class="mono">${esc(serialTarget)}</span> before you can register this device.<br>
          Evidence ID: <span class="mono">${esc(d.evidence_id || d.doc_id || '—')}</span>
        </div>
        <div style="margin-top:16px"><a class="button ghost" href="dashboard.html">Return to Dashboard</a></div>
      `;
      toast('Purchase proof submitted for review.');
    } catch (x) {
      $('#new-kyc-result').innerHTML = `<div class="notice error">${esc(x.message)}</div>`;
      toast(x.message, 'error');
    } finally {
      setBusy(e.submitter || submitKycBtn, false);
    }
  });

}

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
  // Replace rather than duplicate the alert stack when uploads trigger a refresh.
  root.querySelector('#notif-stack')?.remove();

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

    // Role credential history is newest-first from the API. Show only the newest
    // submission for each role-credential type so an old rejection does not look current
    // after the user submits a replacement that is later accepted.
    const seenRoleCredentialTypes = new Set();
    const latestRoleCredentialDocIds = new Set();
    docs.forEach(doc => {
      if (doc.subject_type !== 'USER_ROLE_CREDENTIAL' || !doc.evidence_type) return;
      if (seenRoleCredentialTypes.has(doc.evidence_type)) return;
      seenRoleCredentialTypes.add(doc.evidence_type);
      latestRoleCredentialDocIds.add(doc.doc_id || doc.evidence_id);
    });

    // Beautiful UI Wrapper for Alerts
    const buildNotice = (id, type, icon, title, desc, actionHtml = '') => {
      const closeBtn = `<button class="close-notif" data-id="${esc(id)}" style="position:absolute; top:12px; right:12px; background:none; border:none; font-size:1.4rem; cursor:pointer; opacity:0.4; transition:opacity 0.2s; line-height:1; padding:4px;">&times;</button>`;
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
      if (doc.subject_type === 'USER_ROLE_CREDENTIAL' && doc.evidence_type &&
          !latestRoleCredentialDocIds.has(doc.doc_id || doc.evidence_id)) return;
      // Dismiss each status transition separately so a previously dismissed PENDING/ACCEPTED
      // notification cannot hide a later status for the same record.
      const notificationId = `${doc.doc_id || doc.evidence_id}:${doc.status || 'UNKNOWN'}`;
      if (dismissed.includes(notificationId)) return;
      hasVisible = true;

      if (doc.status === 'PENDING') {
        alertHtml += buildNotice(
          notificationId, 'info', '⏳', 
          'Document Review Pending', 
          `Your upload for ${doc.serial_for_device ? `<span class="mono">${esc(doc.serial_for_device)}</span>` : 'account verification'} is currently being reviewed by the admin team.`
        );
      } else if (doc.status === 'ACCEPTED' && doc.serial_for_device) {
        alertHtml += buildNotice(
          notificationId, 'success', '✅',
          'Purchase Proof Approved',
          `Your invoice for <span class="mono">${esc(doc.serial_for_device)}</span> was verified. You can now mint this device's passport on-chain.`,
          `<a class="button primary small" href="register-device.html?serial=${encodeURIComponent(doc.serial_for_device)}">Register Device Now</a>`
        );
      } else if (doc.status === 'ACCEPTED') {
        const isRoleCredential = doc.subject_type === 'USER_ROLE_CREDENTIAL' ||
          ['IDENTITY_PROOF', 'RECYCLER_AUTHORIZATION', 'BRAND_AUTHORIZATION', 'BUSINESS_REGISTRATION'].includes(doc.evidence_type);
        const requiresSeparateActivation = isRoleCredential &&
          ['RECYCLER', 'VERIFIABLE'].includes(me?.role) && me?.status === 'KYC_IN_PROGRESS';
        const title = requiresSeparateActivation
          ? 'Credential Accepted — Account Approval Pending'
          : (isRoleCredential && me?.status === 'ACTIVE' ? 'Credential Accepted — Account Active' : 'Document Accepted');
        const suffix = requiresSeparateActivation
          ? ' Your account activation request is waiting for the admin’s final approval.'
          : (isRoleCredential && me?.status === 'ACTIVE' ? ' Your account is active.' : '');
        alertHtml += buildNotice(
          notificationId, 'success', '✅',
          title,
          `${esc(doc.evidence_type || doc.doc_type || 'Account credential')} was inspected and accepted by the admin team.${suffix}`
        );
      } else if (doc.status === 'EXPIRED') {
        alertHtml += buildNotice(
          notificationId, 'warning', '📅',
          'Credential Expired — Re-verification Required',
          `Your ${esc(doc.evidence_type || doc.doc_type || 'role credential')} has expired. Upload a renewed certificate in the Evidence Management panel below; privileged operations stay blocked until admin approval.`,
          `<a class="button primary small" href="#credential-renewal-panel">Upload renewal</a>`
        );
      } else if (doc.status === 'REVOKED') {
        alertHtml += buildNotice(
          notificationId, 'error', '⛔',
          'Credential Revoked',
          `This credential was revoked by an administrator. Contact the TraceLoop admin team before submitting replacement evidence.`
        );
      } else if (doc.status === 'REJECTED') {
        alertHtml += buildNotice(
          notificationId, 'error', '❌',
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
