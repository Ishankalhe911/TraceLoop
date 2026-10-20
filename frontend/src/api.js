const DEFAULT_API_BASE = 'https://trace-loop.onrender.com';

export const API_BASE = (localStorage.getItem('traceloop_api_base') || import.meta.env.VITE_API_BASE || DEFAULT_API_BASE).replace(/\/+$/, '');
export const TOKEN_KEY = 'traceloop_access_token';
export const REFRESH_KEY = 'traceloop_refresh_token';
export const USER_KEY = 'traceloop_user';

export function unwrap(payload) {
  return payload?.data !== undefined ? payload.data : payload;
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function getCachedUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null');
  } catch {
    return null;
  }
}

export function saveSession(payload) {
  if (payload?.access_token) localStorage.setItem(TOKEN_KEY, payload.access_token);
  if (payload?.refresh_token) localStorage.setItem(REFRESH_KEY, payload.refresh_token);
  if (payload?.user_id || payload?.role || payload?.status) {
    localStorage.setItem(USER_KEY, JSON.stringify({
      ...(getCachedUser() || {}),
      user_id: payload.user_id,
      role: payload.role,
      status: payload.status,
    }));
  }
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
}

function errorMessage(body, fallback) {
  const detail = body?.detail ?? body?.error?.message ?? body?.error ?? body?.message;
  if (Array.isArray(detail)) return detail.map((item) => item?.msg || JSON.stringify(item)).join(' · ');
  if (detail && typeof detail === 'object') return JSON.stringify(detail);
  return typeof detail === 'string' ? detail : fallback;
}

async function parseBody(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { message: text };
  }
}

function buildHeaders(options, accessToken = getToken()) {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (accessToken && options.auth !== false) headers.set('Authorization', `Bearer ${accessToken}`);
  else headers.delete('Authorization');
  return headers;
}

export async function api(path, options = {}) {
  const requestOptions = { ...options };
  const headers = buildHeaders(requestOptions);
  let response = await fetch(`${API_BASE}${path}`, { ...requestOptions, headers });

  if (response.status === 401 && options.auth !== false && localStorage.getItem(REFRESH_KEY)) {
    try {
      const refreshed = await fetch(`${API_BASE}/api/v1/token/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: localStorage.getItem(REFRESH_KEY) }),
      });
      const refreshBody = unwrap(await parseBody(refreshed));
      if (refreshed.ok && refreshBody?.access_token) {
        localStorage.setItem(TOKEN_KEY, refreshBody.access_token);
        if (refreshBody.refresh_token) localStorage.setItem(REFRESH_KEY, refreshBody.refresh_token);
        response = await fetch(`${API_BASE}${path}`, {
          ...requestOptions,
          headers: buildHeaders(requestOptions, refreshBody.access_token),
        });
      } else {
        clearSession();
      }
    } catch {
      clearSession();
    }
  }

  const body = await parseBody(response);
  if (!response.ok) {
    const error = new Error(errorMessage(body, `Request failed (${response.status}).`));
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

export async function apiData(path, options = {}) {
  return unwrap(await api(path, options));
}

export async function apiBlob(path) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!response.ok) {
    const body = await parseBody(response);
    throw new Error(errorMessage(body, `Could not open file (${response.status}).`));
  }
  return response.blob();
}

export async function copyText(value) {
  const text = String(value ?? '');
  if (!text) throw new Error('Nothing to copy.');
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const field = document.createElement('textarea');
  field.value = text;
  field.setAttribute('readonly', '');
  field.style.position = 'fixed';
  field.style.opacity = '0';
  document.body.appendChild(field);
  field.select();
  const copied = document.execCommand('copy');
  field.remove();
  if (!copied) throw new Error('Copy failed. Select and copy the ID manually.');
}

export function normalizeSerial(value) {
  return String(value || '').trim().toUpperCase().replace(/[^A-Z0-9-]/g, '');
}

export function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

export function displayState(value) {
  return String(value || 'UNKNOWN').replaceAll('_', ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}
