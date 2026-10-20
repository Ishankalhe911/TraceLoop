import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  Activity, ArrowDownLeft, ArrowLeftRight, ArrowRight, BadgeCheck, Check, CheckCircle2,
  CircleAlert, ClipboardCheck, Copy, FileCheck2, FileText, History, Laptop, Leaf,
  LockKeyhole, PackageCheck, Plus, RefreshCw, Recycle, Search, ShieldCheck, Smartphone,
  Trash2, UploadCloud, UserRoundCheck, Users, Wrench, XCircle, BookOpen, CircleHelp,
} from 'lucide-react';
import { api, apiBlob, apiData, displayState, formatDate, normalizeSerial } from './api.js';
import { useAuth, useToast } from './context.jsx';
import {
  Badge, Button, ConfirmModal, CopyButton, EmptyState, Field, Modal, Notice,
  Panel, SelectInput, TextArea, TextInput,
} from './ui.jsx';

const DEMO_PASSPORT_ID = String(import.meta.env.VITE_DEMO_PASSPORT_ID || '').trim().toUpperCase();
const MAX_EVIDENCE_BYTES = 5 * 1024 * 1024;

const ROLE_OPTIONS = [
  { value: 'FIRST_BUYER', label: 'First buyer', description: 'Register a device you purchased.' },
  { value: 'BUYER', label: 'Buyer', description: 'Receive and own devices.' },
  { value: 'RESELLER', label: 'Reseller', description: 'Manage devices for resale.' },
  { value: 'VERIFIABLE', label: 'Authorized service centre', description: 'Inspect devices and issue verification stamps.' },
  { value: 'RECYCLER', label: 'Recycler', description: 'Receive devices and complete the recycling lifecycle.' },
];
const ROLE_EVIDENCE = {
  FIRST_BUYER: { docType: 'PURCHASE_PROOF', label: 'Purchase invoice or receipt', needsSerial: true },
  BUYER: { docType: 'IDENTITY_PROOF', label: 'Identity document', needsSerial: false },
  RESELLER: { docType: 'BUSINESS_REGISTRATION', label: 'Business registration proof', needsSerial: false },
  VERIFIABLE: { docType: 'BRAND_AUTHORIZATION', label: 'Service-centre authorization', needsSerial: false },
  RECYCLER: { docType: 'RECYCLER_AUTHORIZATION', label: 'Recycler authorization', needsSerial: false },
};
const BRANDS = [
  { code: 'DL', name: 'Dell' }, { code: 'HP', name: 'HP' }, { code: 'LN', name: 'Lenovo' },
  { code: 'AS', name: 'ASUS' }, { code: 'AC', name: 'Acer' }, { code: 'AP', name: 'Apple' },
  { code: 'MS', name: 'MSI' }, { code: 'SG', name: 'Samsung' },
];
const HARDWARE_PRESETS = {
  processor: ['Intel Core i3', 'Intel Core i5', 'Intel Core i7', 'Intel Core Ultra 5', 'Intel Core Ultra 7', 'AMD Ryzen 5', 'AMD Ryzen 7', 'Apple M1', 'Apple M2', 'Apple M3', 'Apple M4'],
  ram: ['4GB DDR4', '8GB DDR4', '16GB DDR4', '8GB DDR5', '16GB DDR5', '32GB DDR5', '64GB DDR5'],
  storage: ['256GB SSD', '512GB NVMe SSD', '1TB NVMe SSD', '2TB NVMe SSD', '256GB HDD', '512GB HDD', '1TB HDD'],
  display: ['13.3-inch FHD', '14-inch FHD', '14-inch 2.2K', '15.6-inch FHD', '16-inch 2.5K', '13-inch Retina', '14-inch Retina'],
};

function PageIntro({ eyebrow, title, description, actions }) {
  return <div className="page-intro"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{actions ? <div className="intro-actions">{actions}</div> : null}</div>;
}

function SectionTitle({ icon: Icon, title, detail, count }) {
  return <div className="section-title"><span className="section-icon"><Icon size={18} /></span><div><h3>{title}</h3>{detail && <p>{detail}</p>}</div>{count !== undefined && <Badge>{count}</Badge>}</div>;
}

function StatCard({ icon: Icon, label, value, detail, tone = 'green' }) {
  return <div className={`stat-card stat-${tone}`}><div className="stat-top"><span className="stat-icon"><Icon size={18} /></span><span className="stat-label">{label}</span></div><strong>{value}</strong>{detail && <p>{detail}</p>}</div>;
}

function StateBadge({ value }) {
  const normal = String(value || 'UNKNOWN').toUpperCase();
  const tone = ['VERIFIED', 'CONFIRMED', 'COMPLETED', 'RECEIVED', 'ACTIVE', 'MATCH', 'REGISTERED'].includes(normal) ? 'green'
    : ['CONFLICT', 'RECONCILIATION_REQUIRED', 'PENDING', 'IN_PROGRESS', 'UNDER_REVIEW', 'PENDING_REGISTRATION'].includes(normal) ? 'amber'
    : ['REJECTED', 'FAILED', 'CANCELLED', 'DISPUTED', 'RECYCLED', 'EXPORTED'].includes(normal) ? 'red' : 'neutral';
  return <Badge tone={tone}>{displayState(normal)}</Badge>;
}

function InlineError({ message, onRetry }) {
  if (!message) return null;
  return <div className="inline-error"><CircleAlert size={17} /><div><strong>Couldn't complete that request</strong><p>{message}</p>{onRetry && <button className="text-button" onClick={onRetry}>Try again</button>}</div></div>;
}

function FullPageLoader({ label = 'Loading TraceLoop…' }) {
  return <div className="page-loader"><span className="spinner" /><p>{label}</p></div>;
}

export function LandingPage() {
  const { profile } = useAuth();
  return <>
    <section className="hero page-wrap">
      <div className="hero-copy"><span className="eyebrow"><span className="eyebrow-dot" /> Device lifecycle, made traceable</span><h1>Every device has a story. <span>Make it verifiable.</span></h1><p className="hero-description">TraceLoop connects device owners, authorized service centres, and recyclers through a lifecycle passport that records what happened and where the evidence came from.</p><div className="hero-actions"><Link to={profile ? '/dashboard' : '/track?demo=1'} className="button button-primary button-lg">{profile ? 'Open workspace' : 'Explore a sample passport'} <ArrowRight size={17} /></Link><Link to={profile ? '/track?demo=1' : '/track'} className="button button-secondary button-lg">{profile ? 'Explore sample passport' : 'Track a passport'}</Link>{!profile && <Link to="/register" className="hero-tertiary-link">New to TraceLoop? Get started</Link>}</div><div className="hero-proof"><span><ShieldCheck size={15} /> Owner-authorized inspections</span><span><History size={15} /> Auditable lifecycle events</span></div></div>
      <div className="hero-visual"><div className="device-illustration"><div className="device-camera" /><div className="device-screen"><div className="device-screen-top"><span>TRACELOOP PASSPORT</span><Badge tone="green">V2</Badge></div><div className="device-art"><Laptop size={82} strokeWidth={1.2} /></div><div className="screen-lines"><i /><i /><i /></div><div className="screen-state"><span className="state-dot" /> Lifecycle record ready</div></div><div className="device-base" /></div><div className="floating-chip chip-top"><ShieldCheck size={16} /><span><strong>Verified events</strong><small>Readable history</small></span></div><div className="floating-chip chip-bottom"><Recycle size={16} /><span><strong>End-of-life ready</strong><small>Recorded responsibly</small></span></div><div className="hero-orbit orbit-one" /><div className="hero-orbit orbit-two" /></div>
    </section>
    <section className="value-section page-wrap"><div className="section-heading"><span className="eyebrow">One lifecycle passport</span><h2>Built around the people who handle devices.</h2><p>The workflow stays physical-first. TraceLoop makes the records and handoffs easier to manage.</p></div><div className="value-grid"><article className="value-card"><span className="value-icon"><Laptop size={21} /></span><h3>Owners & buyers</h3><p>Register a device, share its passport for inspection, transfer ownership, and raise a dispute when something needs review.</p></article><article className="value-card"><span className="value-icon"><ShieldCheck size={21} /></span><h3>Authorized service centres</h3><p>Work from owner-shared inspection requests. Record a stamp only after the physical device has been inspected.</p></article><article className="value-card"><span className="value-icon"><Recycle size={21} /></span><h3>Recycling facilities</h3><p>Manage incoming handoffs, acknowledge physical receipt, and record the terminal recycling event.</p></article></div></section>
    <section className="closing-cta page-wrap"><div><span className="eyebrow">Start with a passport ID</span><h2>Know what the record says.</h2><p>Look up a passport to review available lifecycle events and the reconciliation between source records.</p></div><Link to="/track" className="button button-light button-lg">Track a passport <ArrowRight size={16} /></Link></section>
  </>;
}

export function LoginPage() {
  const { signIn, refreshProfile } = useAuth();
  const { toast } = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const [phone, setPhone] = useState(new URLSearchParams(location.search).get('phone') || '');
  const [otp, setOtp] = useState('');
  const [sent, setSent] = useState(false);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const sendCode = async (event) => {
    event?.preventDefault?.();
    setBusy(true); setError(''); setMessage('');
    try {
      const data = await apiData('/api/v1/otp/send', { method: 'POST', auth: false, body: JSON.stringify({ phone: phone.trim() }) });
      const controlledDemoCode = data.demo_otp_enabled === true && typeof data.otp_preview === 'string' && data.otp_preview.trim();
      const deliveryFailed = data.delivery_confirmed === false && !controlledDemoCode;
      setSent(!deliveryFailed);
      setMessage(deliveryFailed ? '' : (data.message || 'Check your phone for a sign-in code.'));
      if (controlledDemoCode) {
        // Developer/demo preview is deliberately console-only, never rendered in the page.
        // The backend must keep OTP preview disabled outside a controlled demo environment.
        console.info('[TraceLoop developer-mode OTP preview]', data.otp_preview);
      }
      if (deliveryFailed) setError(data.message || 'OTP delivery was not confirmed. You can retry or check the phone number.');
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const verifyCode = async (event) => {
    event.preventDefault();
    setBusy(true); setError('');
    try {
      const data = await apiData('/api/v1/otp/verify', { method: 'POST', auth: false, body: JSON.stringify({ phone: phone.trim(), otp_code: otp.trim() }) });
      signIn(data);
      let profile = null;
      try { profile = await refreshProfile(); } catch { /* Keep the token response and allow a later profile retry. */ }
      toast(data.message || 'Signed in.');
      const requested = location.state?.from;
      if (profile?.status === 'ACTIVE' || data.status === 'ACTIVE') navigate(requested || (data.role === 'ADMIN' ? '/admin' : '/dashboard'), { replace: true });
      else navigate('/onboarding', { replace: true });
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  return <section className="auth-layout page-wrap"><div className="auth-aside"><span className="eyebrow"><LockKeyhole size={14} /> Secure sign-in</span><h1>Your lifecycle workspace, in one place.</h1><p>Use the phone number linked to your TraceLoop account. Account access is enabled after the required review and approval steps.</p><div className="auth-points"><span><CheckCircle2 size={16} /> Role-based workspaces</span><span><CheckCircle2 size={16} /> Owner-authorized actions</span><span><CheckCircle2 size={16} /> Auditable lifecycle records</span></div><div className="auth-aside-mark"><Activity size={92} strokeWidth={0.9} /></div></div><div className="auth-card"><div className="auth-card-head"><span className="eyebrow">Welcome back</span><h2>Sign in to TraceLoop</h2><p>We'll send a one-time code to your registered phone.</p></div>{error && <Notice kind="error">{error}</Notice>}{message && !error && <Notice kind="info">{message}</Notice>}
    {!sent ? <form className="form-stack" onSubmit={sendCode}><TextInput label="Phone number" type="tel" autoComplete="tel" inputMode="tel" placeholder="+91 98765 43210" value={phone} onChange={(e) => setPhone(e.target.value)} required maxLength={15} /><Button type="submit" loading={busy} className="full-width">Send sign-in code <ArrowRight size={16} /></Button></form> : <form className="form-stack" onSubmit={verifyCode}><TextInput label="One-time code" inputMode="numeric" autoComplete="one-time-code" placeholder="Enter 6-digit code" value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))} required maxLength={6} /><Button type="submit" loading={busy} className="full-width">Verify and continue <ArrowRight size={16} /></Button><div className="otp-secondary-actions"><button className="text-button" type="button" disabled={busy} onClick={() => sendCode()}>Resend code</button><button className="text-button" type="button" onClick={() => { setSent(false); setOtp(''); setError(''); setMessage(''); }}>Use a different phone</button></div></form>}
    <p className="auth-bottom">New to TraceLoop? <Link to="/register">Create an account</Link></p></div></section>;
}

export function RegisterPage() {
  const { toast } = useToast();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: '', phone: '', role: 'FIRST_BUYER', aadhaar_last4: '', gst_number: '', cpcb_number: '', brand_auth_code: '', service_center_id: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const selected = ROLE_OPTIONS.find((item) => item.value === form.role);
  const update = (key) => (event) => setForm((old) => ({ ...old, [key]: event.target.value }));
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError('');
    const payload = { ...form, name: form.name.trim(), phone: form.phone.trim(), aadhaar_last4: form.aadhaar_last4.trim() };
    ['gst_number', 'cpcb_number', 'brand_auth_code', 'service_center_id'].forEach((key) => { if (!payload[key]) delete payload[key]; });
    try {
      await apiData('/api/v1/register', { method: 'POST', auth: false, body: JSON.stringify(payload) });
      toast('Account created. Verify your phone to continue.');
      navigate(`/login?phone=${encodeURIComponent(payload.phone)}`);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  return <section className="auth-layout page-wrap register-layout"><div className="auth-aside"><span className="eyebrow">Create your account</span><h1>Join the device lifecycle network.</h1><p>Select the role that describes how you participate. Role-specific evidence and admin review remain part of onboarding.</p><div className="role-preview"><div className="role-preview-icon"><ShieldCheck size={21} /></div><div><strong>{selected?.label}</strong><p>{selected?.description}</p></div></div></div><div className="auth-card auth-card-wide"><div className="auth-card-head"><h2>Set up your account</h2><p>We'll use these details to prepare the right onboarding steps.</p></div>{error && <Notice kind="error">{error}</Notice>}<form onSubmit={submit} className="form-stack"><TextInput label="Full name" value={form.name} onChange={update('name')} maxLength={120} autoComplete="name" required placeholder="Name as used in your documents"/><TextInput label="Phone number" value={form.phone} onChange={update('phone')} maxLength={15} autoComplete="tel" required placeholder="+91 98765 43210"/><SelectInput label="How will you use TraceLoop?" value={form.role} onChange={update('role')}>{ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</SelectInput><div className="role-description">{selected?.description}</div><TextInput label="Last 4 digits required by onboarding" value={form.aadhaar_last4} onChange={(e) => setForm((old) => ({ ...old, aadhaar_last4: e.target.value.replace(/\D/g, '').slice(0, 4) }))} inputMode="numeric" maxLength={4} help="Only the last four digits are collected by this form. Do not enter a full government ID number." required />
      {form.role === 'RESELLER' && <TextInput label="GST number" value={form.gst_number} onChange={update('gst_number')} required maxLength={20} />}
      {form.role === 'RECYCLER' && <TextInput label="CPCB / SPCB authorization number" value={form.cpcb_number} onChange={update('cpcb_number')} required maxLength={40} />}
      {form.role === 'VERIFIABLE' && <><TextInput label="Authorization reference" value={form.brand_auth_code} onChange={update('brand_auth_code')} required maxLength={60} /><TextInput label="Service-centre ID" value={form.service_center_id} onChange={update('service_center_id')} required maxLength={60} help="Use the identifier supplied by your organization." /></>}
      <Button type="submit" loading={busy} className="full-width">Create account <ArrowRight size={16} /></Button></form><p className="auth-bottom">Already registered? <Link to="/login">Sign in</Link></p></div></section>;
}

export function OnboardingPage() {
  const { profile, refreshProfile } = useAuth();
  const { toast } = useToast();
  const [docType, setDocType] = useState(ROLE_EVIDENCE[profile?.role]?.docType || 'IDENTITY_PROOF');
  const [serial, setSerial] = useState('');
  const [file, setFile] = useState(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [claimedNumber, setClaimedNumber] = useState('');
  const [issuer, setIssuer] = useState('');
  const [issuedAt, setIssuedAt] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [documents, setDocuments] = useState([]);
  const [submitted, setSubmitted] = useState(false);
  const metadata = ROLE_EVIDENCE[profile?.role] || ROLE_EVIDENCE.BUYER;

  const loadDocuments = useCallback(async () => {
    const data = await apiData('/api/v1/kyc/my-documents');
    setDocuments(data.documents || []);
  }, []);
  useEffect(() => { loadDocuments().catch(() => {}); }, [loadDocuments]);

  const upload = async (event) => {
    event.preventDefault(); setBusy(true); setError('');
    if (!file) { setError('Select the actual document file to upload.'); setBusy(false); return; }
    if (file.size > MAX_EVIDENCE_BYTES) { setError('This file is larger than 5 MB. Choose a smaller PDF or image.'); setBusy(false); return; }
    const form = new FormData();
    form.append('doc_type', metadata.docType);
    form.append('file', file, file.name);
    if (metadata.needsSerial) form.append('serial_for_device', normalizeSerial(serial));
    if (claimedNumber.trim()) form.append('claimed_document_number', claimedNumber.trim());
    if (issuer.trim()) form.append('claimed_issuer', issuer.trim());
    if (issuedAt) form.append('issued_at', issuedAt);
    try {
      await api('/api/v1/evidence/upload', { method: 'POST', body: form });
      setSubmitted(true); setFile(null); setFileInputKey((key) => key + 1); setSerial(''); setClaimedNumber(''); setIssuer(''); setIssuedAt('');
      await loadDocuments();
      toast('Evidence submitted for admin review.');
      await refreshProfile();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  if (profile?.status === 'ACTIVE') return <section className="page-wrap narrow"><Notice kind="success">Your account is active.</Notice><div className="empty-state"><span className="empty-icon"><CheckCircle2 size={24} /></span><h2>You're ready to continue</h2><p>Your account has been approved. Open your workspace to continue your TraceLoop workflow.</p><Link className="button button-primary" to="/dashboard">Open workspace <ArrowRight size={16} /></Link></div></section>;

  return <section className="page-wrap"><PageIntro eyebrow="Account setup" title="Complete your onboarding" description="Upload the actual role-specific document. An administrator reviews it before the related features are enabled."/><div className="onboarding-grid"><div className="onboarding-main"><Panel title={metadata.label} description={`Account status: ${displayState(profile?.status || 'KYC_IN_PROGRESS')}`}><div className="info-callout"><ShieldCheck size={18}/><p>TraceLoop stores evidence privately. An upload is not the same as approval; the backend remains authoritative for account access.</p></div>{error && <Notice kind="error">{error}</Notice>}{submitted && <Notice kind="success">The file was submitted. Your account stays under review until the required approval is complete.</Notice>}<form onSubmit={upload} className="form-stack" encType="multipart/form-data">{metadata.needsSerial && <TextInput label="Device serial number as printed on the invoice" value={serial} onChange={(e) => setSerial(e.target.value)} maxLength={80} required help="The serial is normalized before upload and must match the approved purchase proof used at registration."/>}<div className="file-drop"><UploadCloud size={22}/><strong>{file ? file.name : 'Choose your document'}</strong><p>PDF, JPG, PNG or WebP · maximum 5 MB</p><input key={fileInputKey} type="file" accept=".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/jpeg,image/png,image/webp" onChange={(e) => { const selected = e.target.files?.[0] || null; setFile(selected); setError(selected && selected.size > MAX_EVIDENCE_BYTES ? 'This file is larger than 5 MB. Choose a smaller PDF or image.' : ''); }} required/></div><div className="form-grid"><TextInput label="Document number (optional)" value={claimedNumber} onChange={(e) => setClaimedNumber(e.target.value)} maxLength={80}/><TextInput label="Issuing authority (optional)" value={issuer} onChange={(e) => setIssuer(e.target.value)} maxLength={120}/><TextInput label="Issue date (optional)" type="date" value={issuedAt} onChange={(e) => setIssuedAt(e.target.value)} /></div><Button type="submit" loading={busy} disabled={!file} className="full-width">Submit evidence for review <ArrowRight size={16}/></Button></form></Panel></div><aside className="onboarding-side"><Panel title="Review steps"><ol className="step-list"><li className="step-complete"><span>1</span><div><strong>Phone verified</strong><small>Your phone is attached to this account.</small></div></li><li className={submitted ? 'step-complete' : 'step-current'}><span>2</span><div><strong>Upload evidence</strong><small>Submit the role-specific document above.</small></div></li><li><span>3</span><div><strong>Admin review</strong><small>An admin checks the actual document.</small></div></li><li><span>4</span><div><strong>Account activation</strong><small>Protected features unlock when approved.</small></div></li></ol></Panel><Panel title="Submitted documents" description="Status refreshed from the backend.">{documents.length ? <div className="document-list">{documents.slice(0, 5).map((doc) => <div className="document-row" key={doc.evidence_id || doc.doc_id}><span className="document-icon"><FileText size={16}/></span><div><strong>{doc.evidence_type || doc.doc_type}</strong><small>{doc.serial_for_device || formatDate(doc.uploaded_at)}</small></div><StateBadge value={doc.verification_status || doc.status}/></div>)}</div> : <p className="muted">No documents submitted yet.</p>}</Panel></aside></div></section>;
}

function useMyDevices(includeLegacy = false) {
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const refresh = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const data = await apiData('/devices/my-devices');
      const rows = data.devices || [];
      setDevices(includeLegacy ? rows : rows.filter((row) => !row.is_legacy_test && row.identity_version !== 'V1_LEGACY'));
      return rows;
    } catch (e) { setError(e.message); throw e; }
    finally { setLoading(false); }
  }, [includeLegacy]);
  useEffect(() => { refresh().catch(() => {}); }, [refresh]);
  return { devices, setDevices, loading, error, refresh };
}

function DeviceSummary({ device, children }) {
  const { toast } = useToast();
  const config = device.current_config || {};
  const entries = Object.entries(config).slice(0, 4);
  const publicId = device.public_id || device.device_id;
  return <article className="device-card panel-card">
    <div className="device-card-top"><span className="device-brand-mark"><Laptop size={19} /></span><StateBadge value={device.status || device.device_status || 'UNKNOWN'} /><span className="grow" /><CopyButton value={publicId} onCopy={toast} /></div>
    <h3>{device.brand_name || device.brand || 'Device'}</h3>
    <p className="mono device-id">{publicId}</p>
    <div className="device-meta"><span>Stamp {device.stamp_valid ? 'valid' : 'not valid'}</span><span>{device.registration_status === 'CONFIRMED' ? 'Registration confirmed' : displayState(device.registration_status || 'UNKNOWN')}</span></div>
    {entries.length > 0 && <div className="spec-list">{entries.map(([key, value]) => <div className="spec-row" key={key}><span>{displayState(key)}</span><strong>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</strong></div>)}</div>}
    <div className="device-card-actions">{children}</div>
  </article>;
}

function DeviceOptions({ devices, value, onChange, label = 'Select device', includeExternal = false, disabled = false, help }) {
  return <SelectInput label={label} value={value} onChange={onChange} disabled={disabled} help={help}>
    <option value="">Choose a device…</option>
    {devices.map((device) => <option key={device.public_id || device.device_id} value={device.public_id || device.device_id}>{`${device.brand_name || device.brand || 'Device'} · ${device.public_id || device.device_id}`}</option>)}
    {includeExternal && <option value="__external__">Enter another public Passport ID…</option>}
  </SelectInput>;
}

function TransferModal({ open, device, onClose, onSuccess }) {
  const { toast } = useToast();
  const [recipient, setRecipient] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (open) { setRecipient(''); setError(''); } }, [open, device?.public_id, device?.device_id]);
  const submit = async (event) => {
    event.preventDefault();
    if (!device) return;
    const to = recipient.trim();
    if (!to) { setError('Enter the recipient’s TraceLoop user ID.'); return; }
    if (!/^[a-zA-Z0-9-]{5,36}$/.test(to)) { setError('Check the ID. Paste the recipient’s TraceLoop user ID exactly as shown in their workspace.'); return; }
    setBusy(true); setError('');
    try {
      const data = await apiData('/transfers/initiate', { method: 'POST', body: JSON.stringify({ device_id: device.public_id || device.device_id, to_user_id: to }) });
      toast(data.message || 'Transfer initiated. Waiting for recipient acceptance.', 'success');
      onClose(); onSuccess?.();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  return <Modal open={open} title="Initiate ownership transfer" description="Meet the recipient and exchange IDs in person. The recipient must accept, and an authorized verifier completes the physical handoff." onClose={onClose} size="md">
    {device && <div className="selected-device-strip"><Laptop size={19}/><div><strong>{device.brand_name || device.brand || 'Device'}</strong><small className="mono">{device.public_id || device.device_id}</small></div><CopyButton value={device.public_id || device.device_id} onCopy={toast}/></div>}
    <Notice kind="info">The device must have a valid verification stamp. If initiation is rejected, arrange re-verification first.</Notice>
    {error && <Notice kind="error">{error}</Notice>}
    <form className="form-stack" onSubmit={submit}><TextInput label="Recipient TraceLoop user ID" value={recipient} onChange={(e) => setRecipient(e.target.value.trimStart())} autoComplete="off" spellCheck="false" maxLength={36} required placeholder="Paste the ID shared by the recipient" help="The recipient can copy this from their account header. No public user directory is used."/><div className="modal-actions"><Button variant="secondary" type="button" onClick={onClose} disabled={busy}>Cancel</Button><Button type="submit" loading={busy}>Initiate transfer <ArrowRight size={16}/></Button></div></form>
  </Modal>;
}

function HardwareChangeModal({ open, device, onClose, onSuccess }) {
  const { toast } = useToast();
  const [component, setComponent] = useState('');
  const [customComponent, setCustomComponent] = useState('');
  const [changeType, setChangeType] = useState('UPGRADED');
  const [newSpec, setNewSpec] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const config = device?.current_config || {};
  const keys = Object.keys(config);
  const selectedKey = component === '__custom__' ? customComponent.trim() : component;
  const oldSpec = selectedKey ? config[selectedKey] ?? config[keys.find((key) => key.toLowerCase() === selectedKey.toLowerCase())] : undefined;
  const presets = HARDWARE_PRESETS[(selectedKey || '').toLowerCase()] || [];
  useEffect(() => { if (open) { setComponent(''); setCustomComponent(''); setChangeType('UPGRADED'); setNewSpec(''); setError(''); } }, [open, device?.public_id]);
  const submit = async (event) => {
    event.preventDefault();
    const name = selectedKey;
    if (!name) { setError('Select a component or enter a new component name.'); return; }
    if (changeType === 'REMOVED' && oldSpec == null) { setError('That component is not present in the current configuration.'); return; }
    if (changeType !== 'REMOVED' && !newSpec.trim()) { setError('Enter the new component specification.'); return; }
    setBusy(true); setError('');
    try {
      const data = await apiData(`/device-profiles/${encodeURIComponent(device.public_id || device.device_id)}/hardware-change`, { method: 'POST', body: JSON.stringify({ component: name, change_type: changeType, old_spec: oldSpec == null ? null : String(oldSpec), new_spec: changeType === 'REMOVED' ? 'REMOVED' : newSpec.trim() }) });
      toast(data.message || 'Hardware change recorded. Re-verification is required.'); onClose(); onSuccess?.();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  return <Modal open={open} title="Declare a hardware change" description="TraceLoop records the declared change and invalidates the current verification stamp. A service-centre re-verification is required." onClose={onClose}>
    {device && <div className="selected-device-strip"><Laptop size={19}/><div><strong>{device.brand_name || device.brand}</strong><small className="mono">{device.public_id || device.device_id}</small></div></div>}
    {error && <Notice kind="error">{error}</Notice>}
    <form className="form-stack" onSubmit={submit}>
      <SelectInput label="Component" value={component} onChange={(e) => setComponent(e.target.value)} required><option value="">Select a current component…</option>{keys.map((key) => <option key={key} value={key}>{displayState(key)} · {String(config[key])}</option>)}<option value="__custom__">Add new component…</option></SelectInput>
      {component === '__custom__' && <TextInput label="New component name" value={customComponent} onChange={(e) => setCustomComponent(e.target.value)} required maxLength={80} placeholder="e.g. Battery"/>}
      {selectedKey && <div className="current-spec"><small>Current recorded specification</small><strong>{oldSpec == null ? 'No existing value' : String(oldSpec)}</strong></div>}
      <SelectInput label="Change type" value={changeType} onChange={(e) => { setChangeType(e.target.value); if (e.target.value === 'REMOVED') setNewSpec('REMOVED'); else if (newSpec === 'REMOVED') setNewSpec(''); }}><option value="UPGRADED">Upgraded</option><option value="REPLACED">Replaced</option><option value="ADDED">Added</option><option value="REMOVED">Removed</option></SelectInput>
      {changeType !== 'REMOVED' && <>{presets.length > 0 && <SelectInput label="Suggested specification" value={presets.includes(newSpec) ? newSpec : ''} onChange={(e) => e.target.value && setNewSpec(e.target.value)}><option value="">Choose a preset or type below…</option>{presets.map((p) => <option key={p}>{p}</option>)}</SelectInput>}<TextInput label="New specification" value={newSpec} onChange={(e) => setNewSpec(e.target.value)} required maxLength={200} placeholder="Enter the new hardware specification"/></>}
      <div className="modal-actions"><Button variant="secondary" type="button" onClick={onClose} disabled={busy}>Cancel</Button><Button type="submit" loading={busy}>Record change</Button></div>
    </form>
  </Modal>;
}

function DisputeModal({ open, devices, defaultDevice, onClose, onSuccess }) {
  const { toast } = useToast();
  const [deviceId, setDeviceId] = useState('');
  const [externalId, setExternalId] = useState('');
  const [type, setType] = useState('MISREPRESENTED_SPEC');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (open) { setDeviceId(defaultDevice?.public_id || defaultDevice?.device_id || ''); setExternalId(''); setType('MISREPRESENTED_SPEC'); setDescription(''); setError(''); } }, [open, defaultDevice?.public_id]);
  const submit = async (event) => {
    event.preventDefault();
    const id = deviceId === '__external__' ? externalId.trim().toUpperCase() : deviceId;
    if (!id) { setError('Select a device or enter a public Passport ID.'); return; }
    if (description.trim().length < 10) { setError('Describe the issue in at least 10 characters.'); return; }
    setBusy(true); setError('');
    try { const data = await apiData('/disputes/report', { method: 'POST', body: JSON.stringify({ device_id: id, dispute_type: type, description: description.trim() }) }); toast(data.message || 'Dispute submitted for review.'); onClose(); onSuccess?.(); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  return <Modal open={open} title="Report a device issue" description="An administrator reviews the report before any on-chain freeze. Filing a report does not immediately freeze the device." onClose={onClose}>
    {error && <Notice kind="error">{error}</Notice>}
    <form className="form-stack" onSubmit={submit}><DeviceOptions devices={devices} value={deviceId} onChange={(e) => setDeviceId(e.target.value)} label="Device / Passport" includeExternal help="Select one of your devices or enter an external public Passport ID."/>{deviceId === '__external__' && <TextInput label="Public Passport ID" value={externalId} onChange={(e) => setExternalId(e.target.value.toUpperCase())} required maxLength={40} placeholder="TL-DELL-…"/>}<SelectInput label="Issue type" value={type} onChange={(e) => setType(e.target.value)}><option value="MISREPRESENTED_SPEC">Misrepresented specification</option><option value="STOLEN">Stolen device</option><option value="FAKE_STAMP">Fake verification stamp</option><option value="OWNERSHIP_DISPUTE">Ownership dispute</option><option value="OTHER">Other</option></SelectInput><TextArea label="Describe the issue" value={description} onChange={(e) => setDescription(e.target.value)} required minLength={10} maxLength={4000} rows={4} placeholder="What happened, and what needs to be reviewed?"/><div className="modal-actions"><Button variant="secondary" type="button" onClick={onClose} disabled={busy}>Cancel</Button><Button variant="danger" type="submit" loading={busy}>Submit report</Button></div></form>
  </Modal>;
}

function ShareVerificationModal({ open, device, onClose, onSuccess }) {
  const { toast } = useToast();
  const [verifierId, setVerifierId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (open) { setVerifierId(''); setError(''); } }, [open, device?.public_id]);
  const submit = async (event) => {
    event.preventDefault();
    const id = verifierId.trim();
    if (!id) { setError('Enter the service centre’s TraceLoop user ID.'); return; }
    setBusy(true); setError('');
    try { const data = await apiData('/verification/requests', { method: 'POST', body: JSON.stringify({ public_id: device.public_id || device.device_id, verifier_id: id }) }); toast(data.message || 'Passport shared with the selected service centre.'); onClose(); onSuccess?.(); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  return <Modal open={open} title="Share passport with a service centre" description="Obtain the centre’s TraceLoop user ID during your physical visit. Sharing this passport grants that specific verifier access to the inspection request details." onClose={onClose}>
    {device && <div className="selected-device-strip"><ShieldCheck size={18}/><div><strong>{device.brand_name || device.brand || 'Device'}</strong><small className="mono">{device.public_id || device.device_id}</small></div><CopyButton value={device.public_id || device.device_id} onCopy={toast}/></div>}
    <Notice kind="info">Sharing does not issue a stamp. The service centre must physically inspect the device before issuing one.</Notice>{error && <Notice kind="error">{error}</Notice>}
    <form className="form-stack" onSubmit={submit}><TextInput label="Authorized service-centre user ID" value={verifierId} onChange={(e) => setVerifierId(e.target.value.trimStart())} required maxLength={36} placeholder="Paste ID provided at the counter" help="No public verifier directory is used; the owner explicitly chooses a specific verifier."/><div className="modal-actions"><Button variant="secondary" type="button" onClick={onClose} disabled={busy}>Cancel</Button><Button type="submit" loading={busy}>Share passport</Button></div></form>
  </Modal>;
}

export function RegisterDevicePage() {
  const { toast } = useToast();
  const navigate = useNavigate();
  const [options, setOptions] = useState([]);
  const [optionsLoading, setOptionsLoading] = useState(true);
  const [optionsError, setOptionsError] = useState('');
  const [serial, setSerial] = useState('');
  const [brandCode, setBrandCode] = useState('DL');
  const [config, setConfig] = useState({ processor: '', ram: '8GB DDR4', storage: '512GB NVMe SSD', display: '15.6-inch FHD', condition: 'GOOD' });
  const [customSerial, setCustomSerial] = useState('');
  const [invoice, setInvoice] = useState(null);
  const [invoiceInputKey, setInvoiceInputKey] = useState(0);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const brand = BRANDS.find((b) => b.code === brandCode) || BRANDS[0];
  const loadOptions = useCallback(async () => {
    setOptionsLoading(true); setOptionsError('');
    try { const data = await apiData('/devices/registration-options'); const next = data.options || []; setOptions(next); setSerial((previous) => previous && next.some((o) => o.serial_for_device === previous) ? previous : next[0]?.serial_for_device || ''); }
    catch (e) { setOptionsError(e.message); }
    finally { setOptionsLoading(false); }
  }, []);
  useEffect(() => { loadOptions(); }, [loadOptions]);
  const uploadNewProof = async (event) => {
    event.preventDefault();
    const clean = normalizeSerial(customSerial);
    if (!clean || !invoice) { setError('Enter the serial number and select the purchase invoice.'); return; }
    if (invoice.size > MAX_EVIDENCE_BYTES) { setError('This file is larger than 5 MB. Choose a smaller PDF or image.'); return; }
    setUploadBusy(true); setError('');
    try { const form = new FormData(); form.append('doc_type', 'PURCHASE_PROOF'); form.append('serial_for_device', clean); form.append('file', invoice, invoice.name); const result = await api('/api/v1/evidence/upload', { method: 'POST', body: form }); toast(result?.data?.message || result?.message || 'Invoice uploaded for review.'); setCustomSerial(''); setInvoice(null); setInvoiceInputKey((key) => key + 1); await loadOptions(); }
    catch (e) { setError(e.message); }
    finally { setUploadBusy(false); }
  };
  const submit = async (event) => {
    event.preventDefault();
    if (!serial || serial === '__new__') { setError('Select an approved purchase-proof serial number. New invoices must be approved before registration.'); return; }
    setBusy(true); setError('');
    try { const result = await apiData('/devices/register', { method: 'POST', body: JSON.stringify({ brand_code: brandCode, brand_name: brand.name, serial_raw: serial, original_config: Object.fromEntries(Object.entries(config).filter(([, value]) => String(value).trim()).map(([key, value]) => [key, value.trim()])) }) }); toast(result.message || 'Device registration submitted.'); navigate('/dashboard'); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  return <section className="page-wrap">
    <PageIntro eyebrow="Device registry" title="Register a device" description="Choose a purchase proof already accepted by an administrator. New devices remain subject to the backend's duplicate, evidence, and on-chain registration checks." actions={<Link className="button button-secondary" to="/dashboard">Back to workspace</Link>} />
    <div className="form-layout"><div><Panel title="Device details" description="Select the canonical brand; TraceLoop fills the brand name automatically.">{optionsError && <Notice kind="error">{optionsError}<button className="text-button" onClick={loadOptions} type="button">Retry</button></Notice>}{error && <Notice kind="error">{error}</Notice>}
      <form className="form-stack" onSubmit={submit}><SelectInput label="Approved purchase proof / serial" value={serial} onChange={(e) => setSerial(e.target.value)} disabled={optionsLoading || !options.length} required help={optionsLoading ? 'Loading approved purchase proofs…' : options.length ? 'Only approved, currently unregistered serials are offered.' : 'No eligible serials found. Upload a new invoice and wait for admin review.'}><option value="">Choose an approved serial…</option>{options.map((option) => <option key={option.serial_for_device} value={option.serial_for_device}>{option.serial_for_device}</option>)}</SelectInput>
      <SelectInput label="Brand" value={brandCode} onChange={(e) => setBrandCode(e.target.value)}>{BRANDS.map((b) => <option key={b.code} value={b.code}>{b.name}</option>)}</SelectInput><div className="readonly-field"><span>Brand name sent to backend</span><strong>{brand.name}</strong><small>Derived from the selected brand code.</small></div>
      <div className="section-title-inline"><h3>Original configuration</h3><p>Choose a preset or type the exact specification printed or observed on the device.</p></div>
      {Object.entries({ processor: 'Processor', ram: 'RAM', storage: 'Storage', display: 'Display' }).map(([key, label]) => <div className="hardware-input-row" key={key}><Field label={label}><input className="input" list={`preset-${key}`} value={config[key]} onChange={(e) => setConfig((old) => ({ ...old, [key]: e.target.value }))} required /></Field><datalist id={`preset-${key}`}>{HARDWARE_PRESETS[key].map((v) => <option key={v} value={v}/>)}</datalist></div>)}<SelectInput label="Condition" value={config.condition} onChange={(e) => setConfig((old) => ({ ...old, condition: e.target.value }))}><option value="EXCELLENT">Excellent</option><option value="GOOD">Good</option><option value="FAIR">Fair</option><option value="POOR">Poor</option></SelectInput><Button type="submit" loading={busy} disabled={optionsLoading || !options.length || !serial}>Register device <ArrowRight size={16}/></Button></form>
    </Panel></div><aside className="side-stack"><Panel title="Need to register a new serial?" description="Upload a purchase proof and wait for its review before creating the device passport."><form className="form-stack" onSubmit={uploadNewProof}><TextInput label="Serial number" value={customSerial} onChange={(e) => setCustomSerial(e.target.value)} required maxLength={80} placeholder="Exactly as printed on the invoice"/><Field label="Invoice or purchase proof"><input key={invoiceInputKey} className="input file-input" type="file" accept=".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/jpeg,image/png,image/webp" onChange={(e) => { const selected = e.target.files?.[0] || null; setInvoice(selected); setError(selected && selected.size > MAX_EVIDENCE_BYTES ? 'This file is larger than 5 MB. Choose a smaller PDF or image.' : ''); }} required/></Field><Button variant="secondary" type="submit" loading={uploadBusy} disabled={!invoice}>Upload for review</Button></form><p className="muted compact">Uploading a file does not authorize registration. The device becomes selectable after the purchase proof is accepted.</p></Panel><Panel title="Before you submit"><ul className="plain-list"><li>Use the serial shown on the physical device and invoice.</li><li>Choose its actual current configuration.</li><li>Registration must be confirmed on-chain before lifecycle actions unlock.</li></ul></Panel></aside></div>
  </section>;
}

function IncomingTransfersPanel({ onRefresh }) {
  const { toast } = useToast();
  const [rows, setRows] = useState([]); const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [pending, setPending] = useState(null);
  const load = useCallback(async () => { setLoading(true); setError(''); try { const data = await apiData('/transfers/incoming'); setRows((data.incoming_transfers || []).filter((row) => !row.is_legacy_test && !row.read_only)); } catch (e) { setError(e.message); } finally { setLoading(false); } }, []);
  useEffect(() => { load(); }, [load]);
  const act = async () => { if (!pending) return; const current = pending; setPending(null); try { const data = await apiData(`/transfers/${encodeURIComponent(current.id)}/${current.action}`, { method: 'POST' }); toast(data.message || (current.action === 'accept' ? 'Transfer accepted. The service-centre inspection is still required.' : 'Transfer cancelled.')); await load(); onRefresh?.(); } catch (e) { toast(e.message, 'error'); } };
  return <Panel title="Incoming transfers" description="Accepting moves a handoff to physical inspection; it does not itself complete the on-chain ownership transfer." actions={<Button variant="ghost" size="sm" onClick={load} icon={RefreshCw}>Refresh</Button>}>
    {loading ? <div className="skeleton-stack"><div className="skeleton-row"/><div className="skeleton-row"/></div> : error ? <InlineError message={error} onRetry={load}/> : rows.length ? <div className="list-stack">{rows.map((row) => <div className="work-row" key={row.transfer_id}><div className="work-row-icon"><Laptop size={20}/></div><div className="work-row-main"><strong>{row.brand_name || 'Device'}</strong><small className="mono">{row.device_id}</small><small>{formatDate(row.initiated_at)}</small></div><StateBadge value={row.status}/><div className="row-actions"><Button size="sm" onClick={() => setPending({ id: row.transfer_id, action: 'accept', title: 'Accept this transfer?' })}>Accept</Button><Button size="sm" variant="secondary" onClick={() => setPending({ id: row.transfer_id, action: 'cancel', title: 'Cancel this handoff?', description: 'The transfer will be cancelled and cannot be accepted afterward.' })}>Cancel</Button></div></div>)}</div> : <EmptyState icon={ArrowDownLeft} title="No incoming transfers" description="New handoffs addressed to your account will appear here."/>}
    <ConfirmModal open={!!pending} title={pending?.title || 'Confirm action'} description={pending?.description || (pending?.action === 'accept' ? 'You are accepting the handoff for physical inspection. An authorized verifier must complete the transfer.' : '')} actionLabel={pending?.action === 'accept' ? 'Accept transfer' : 'Cancel transfer'} onCancel={() => setPending(null)} onConfirm={act}/>
  </Panel>;
}

function WorkflowNextStep({ current, next, detail, recovery = '', tone = 'green', actions = null, icon: Icon = ArrowRight }) {
  return <Panel className="next-step-panel" title="Current step → next step">
    <div className={`workflow-step-guidance workflow-step-${tone}`}>
      <div className="workflow-step-stage">
        <span className="workflow-step-stage-icon"><Icon size={17}/></span>
        <span className="workflow-step-label">Current</span>
        <strong>{current}</strong>
      </div>
      <div className="workflow-step-arrow" aria-hidden="true"><ArrowRight size={18}/></div>
      <div className="workflow-step-stage workflow-step-stage-next">
        <span className="workflow-step-label">Next</span>
        <strong>{next}</strong>
        <p>{detail}</p>
        {recovery && <p className="workflow-step-recovery"><strong>If this stalls or fails:</strong> {recovery}</p>}
        {actions && <div className="workflow-step-actions">{actions}</div>}
      </div>
    </div>
  </Panel>;
}

function WorkspaceNextStep({ profile, devices, loading, error, onRefresh, onOpenShare, onOpenTransfer }) {
  const role = profile?.role;
  if (loading) return <Panel className="next-step-panel" title="Current step → next step"><div className="next-step-loading"><span className="spinner"/><span>Checking the latest device states…</span></div></Panel>;
  if (error) return <WorkflowNextStep tone="amber" icon={CircleHelp} current="Latest state unavailable" next="Refresh workspace" detail="The device list did not load. Refresh it before deciding what to do next." recovery="If it still cannot load, try again later. Do not assume an empty or old list reflects the current device state." actions={<Button size="sm" variant="secondary" onClick={onRefresh}>Refresh state <RefreshCw size={15}/></Button>}/>;

  if (role === 'FIRST_BUYER' && devices.length === 0) {
    return <WorkflowNextStep icon={Laptop} current="No device passport yet" next="Get purchase proof approved, then register" detail="If the invoice is not approved yet, upload it for review on the registration page. Once approved, select its serial and complete device registration." recovery="If the proof is rejected, read the reason, correct the document or serial details, and submit it again. Register only after approval." actions={<Link className="button button-primary button-sm" to="/devices/new">Open registration <ArrowRight size={15}/></Link>}/>;
  }

  if (['BUYER', 'RESELLER'].includes(role) && devices.length === 0) {
    return <WorkflowNextStep icon={ArrowLeftRight} current="No device on this account" next="Review incoming transfers" detail="Accept an expected handoff first. Ownership changes only after the required physical handoff is completed and the result is recorded." recovery="If an action fails or the status is unclear, refresh the transfer list before retrying. Do not create a second handoff for the same device." actions={<Link className="button button-primary button-sm" to="/transfer">Open transfers <ArrowRight size={15}/></Link>}/>;
  }

  const terminal = (device) => ['RECYCLED', 'EXPORTED'].includes(String(device.status || '').toUpperCase());
  const stateOf = (device) => String(device.status || '').toUpperCase();
  const registrationOf = (device) => String(device.registration_status || '').toUpperCase();
  const byPriority = (predicate) => devices.find((device) => !terminal(device) && predicate(device));

  const disputedDevice = byPriority((device) => stateOf(device) === 'DISPUTED');
  if (disputedDevice) return <WorkflowNextStep tone="amber" icon={ShieldCheck} current="Device dispute is open" next="Wait for the dispute state to change" detail="Transfers remain blocked while the passport is disputed. After the state changes, review the latest record and check whether its verification stamp is still valid." recovery="If the dispute remains open, do not start a transfer. Refresh the passport to use the latest recorded status." actions={<Link className="button button-secondary button-sm" to={`/track?id=${encodeURIComponent(disputedDevice.public_id || disputedDevice.device_id || '')}`}>Review passport <ArrowRight size={15}/></Link>}/>;

  const needsReconciliation = byPriority((device) => registrationOf(device) === 'RECONCILIATION_REQUIRED');
  if (needsReconciliation) return <WorkflowNextStep tone="amber" icon={CircleAlert} current="Registration outcome is uncertain" next="Reconcile the existing attempt" detail="Open the passport and review its transaction status. The original attempt must be resolved before another registration is submitted." recovery="If a source is unavailable, retry reconciliation when it returns. Do not resubmit an action whose on-chain outcome is unknown." actions={<Link className="button button-secondary button-sm" to={`/track?id=${encodeURIComponent(needsReconciliation.public_id || needsReconciliation.device_id || '')}`}>Review status <ArrowRight size={15}/></Link>}/>;

  const registrationFailed = byPriority((device) => registrationOf(device) === 'FAILED');
  if (registrationFailed) return <WorkflowNextStep tone="amber" icon={CircleAlert} current="Registration failed" next="Review the failed attempt" detail="Read the registration status and error details before deciding whether to retry." recovery="Retry only when the record confirms no transaction was submitted. If submission may have occurred, reconcile the existing attempt first." actions={<Link className="button button-secondary button-sm" to={`/track?id=${encodeURIComponent(registrationFailed.public_id || registrationFailed.device_id || '')}`}>Review registration <ArrowRight size={15}/></Link>}/>;

  const registrationPending = byPriority((device) => ['PENDING', 'SUBMISSION_STARTED'].includes(registrationOf(device)));
  if (registrationPending) return <WorkflowNextStep tone="amber" icon={CircleHelp} current="Registration is processing" next="Wait for registration confirmation" detail="Refresh after a short wait. Device verification comes next only after registration is confirmed; transfers stay unavailable until a valid stamp exists." recovery="If the status remains pending, run reconciliation before attempting another registration." actions={<><Link className="button button-secondary button-sm" to={`/track?id=${encodeURIComponent(registrationPending.public_id || registrationPending.device_id || '')}`}>Check passport <ArrowRight size={15}/></Link><Button size="sm" variant="secondary" onClick={onRefresh}>Refresh <RefreshCw size={15}/></Button></>}/>;

  const needsVerification = byPriority((device) => registrationOf(device) === 'CONFIRMED' && !device.stamp_valid);
  if (needsVerification) return <WorkflowNextStep tone="amber" icon={ShieldCheck} current="Registration confirmed" next="Complete device verification" detail="Submit the passport for inspection through the existing workflow. After the stamp is recorded, check that the latest passport reports it as valid before starting a transfer." recovery="If verification is rejected, read the recorded reason, correct the relevant details, and resubmit. If stamp submission times out or its result is unclear, reconcile before retrying." actions={<Button size="sm" onClick={() => onOpenShare(needsVerification)}>Share for inspection <ArrowRight size={15}/></Button>}/>;

  const ready = byPriority((device) => registrationOf(device) === 'CONFIRMED' && device.stamp_valid);
  if (ready) return <WorkflowNextStep icon={CheckCircle2} current="Registration and verification complete" next="Start a transfer only when needed" detail="The passport has a valid stamp. Review the latest record first; after a transfer is initiated, the recipient must accept and the handoff must be recorded before ownership changes." recovery="If transfer status is uncertain after submission, inspect the existing transfer and reconcile its outcome before initiating another one." actions={<><Link className="button button-secondary button-sm" to={`/track?id=${encodeURIComponent(ready.public_id || ready.device_id || '')}`}>Review passport <ArrowRight size={15}/></Link><Button size="sm" onClick={() => onOpenTransfer(ready)}>Start transfer</Button></>}/>;

  const terminalDevice = devices.find(terminal);
  if (terminalDevice && devices.every(terminal)) return <WorkflowNextStep current={`Lifecycle closed: ${displayState(stateOf(terminalDevice))}`} next="Review the final record" detail="This device is no longer eligible for transfer. The passport retains the final lifecycle events and available evidence references." recovery="If the final event or its evidence appears incomplete, review the recorded sources and reconciliation result; do not attempt another transfer." actions={<Link className="button button-secondary button-sm" to={`/track?id=${encodeURIComponent(terminalDevice.public_id || terminalDevice.device_id || '')}`}>Review lifecycle <ArrowRight size={15}/></Link>}/>;

  return <WorkflowNextStep icon={BookOpen} current="No blocking state detected" next="Review the latest passport" detail="Check the lifecycle state, verification stamp, and reconciliation limitations before starting another lifecycle action." recovery="If a status conflicts with what you expect, refresh the passport and run reconciliation before taking an irreversible action." actions={<Link className="button button-secondary button-sm" to="/track">Track a passport <ArrowRight size={15}/></Link>}/>;
}

export function DashboardPage() {
  const { profile } = useAuth();
  const { toast } = useToast();
  const { devices, loading, error, refresh } = useMyDevices(false);
  const [showLegacy, setShowLegacy] = useState(false);
  const [legacyRows, setLegacyRows] = useState([]);
  const [modal, setModal] = useState({ type: '', device: null });
  const [myDisputes, setMyDisputes] = useState([]);
  const [disputesError, setDisputesError] = useState('');
  const [ecoPointsBalance, setEcoPointsBalance] = useState(Number(profile?.reward_points || 0));
  const role = profile?.role;
  const loadDisputes = useCallback(async () => { try { const data = await apiData('/disputes/my'); setMyDisputes(data.my_disputes || []); setDisputesError(''); } catch (e) { setDisputesError(e.message); } }, []);
  const loadLegacy = useCallback(async () => { try { const data = await apiData('/devices/my-devices'); setLegacyRows((data.devices || []).filter((row) => row.is_legacy_test || row.identity_version === 'V1_LEGACY')); } catch { setLegacyRows([]); } }, []);
  useEffect(() => { if (['FIRST_BUYER','BUYER','RESELLER'].includes(role)) loadDisputes(); }, [role, loadDisputes]);
  useEffect(() => { if (showLegacy) loadLegacy(); }, [showLegacy, loadLegacy]);
  useEffect(() => {
    let cancelled = false;
    apiData('/api/v1/rewards/me?limit=1').then((data) => {
      if (!cancelled && Number.isFinite(Number(data.balance_points))) {
        setEcoPointsBalance(Number(data.balance_points));
      }
    }).catch(() => {
      // The profile balance remains as a compatible fallback if the rewards
      // endpoint is not available in an older backend deployment.
    });
    return () => { cancelled = true; };
  }, []);

  if (!profile) return <FullPageLoader label="Loading account…"/>;
  if (profile.status !== 'ACTIVE' && role !== 'ADMIN') return <section className="page-wrap narrow"><PageIntro eyebrow="Account setup" title="Your account is under review" description="Protected lifecycle actions remain unavailable until required review and approval are complete."/><Panel title="Account status"><div className="status-banner"><StateBadge value={profile.status}/><p>Submitted evidence is reviewed by TraceLoop administrators. You do not need to register again.</p></div><div className="identifier-card"><div><small>Your TraceLoop user ID</small><strong className="mono">{profile.user_id}</strong></div><CopyButton value={profile.user_id} onCopy={toast}/></div><Link className="button button-primary" to="/onboarding">Review onboarding <ArrowRight size={16}/></Link></Panel></section>;
  if (role === 'ADMIN') return <section className="page-wrap"><PageIntro eyebrow="Administration" title="Operations workspace" description="Review evidence and account activation, supervise disputes, and inspect device registry health." actions={<Link className="button button-primary" to="/admin">Open admin console <ArrowRight size={16}/></Link>}/><Panel title="Quick access"><div className="quick-link-grid"><Link to="/admin" className="quick-link"><ShieldCheck/><span><strong>Admin console</strong><small>Approvals, evidence, disputes, and devices</small></span><ArrowRight/></Link><Link to="/track" className="quick-link"><Search/><span><strong>Track passport</strong><small>Public passport and reconciliation view</small></span><ArrowRight/></Link></div></Panel></section>;
  if (role === 'VERIFIABLE') return <VerifierWorkspace/>;
  if (role === 'RECYCLER') return <RecyclingPage />;

  return <section className="page-wrap">
    <PageIntro eyebrow={`${displayState(role)} workspace`} title={`Welcome${profile.name ? `, ${profile.name.split(' ')[0]}` : ''}`} description="Your devices, handoffs, and lifecycle actions in one workspace." actions={role === 'FIRST_BUYER' ? <Link className="button button-primary" to="/devices/new"><Plus size={16}/> Register device</Link> : <Link className="button button-primary" to="/transfer"><ArrowLeftRight size={16}/> Manage transfers</Link>}/>
    <div className="stat-grid modern-stats dashboard-stats"><StatCard icon={Laptop} label="Visible devices" value={devices.length} detail="Confirmed V2 and active records"/><StatCard icon={ShieldCheck} label="Need re-verification" value={devices.filter((d) => d.registration_status === 'CONFIRMED' && !d.stamp_valid && !['RECYCLED','EXPORTED','DISPUTED'].includes(d.status)).length} detail="Stamp is not currently valid" tone="amber"/><StatCard icon={ArrowLeftRight} label="Active role" value={displayState(role)} detail="Role permissions enforced by the backend" tone="blue"/><StatCard icon={Leaf} label="Eco Points" value={ecoPointsBalance.toLocaleString()} detail="Reward balance · history in Account" tone="amber"/></div>
    <WorkspaceNextStep profile={profile} devices={devices} loading={loading} error={error} onRefresh={() => refresh().catch(() => {})} onOpenShare={(device) => setModal({ type: 'share', device })} onOpenTransfer={(device) => setModal({ type: 'transfer', device })}/>
    <div className="workspace-grid"><div className="workspace-primary"><Panel title="My devices" description="Legacy V1 test records are hidden by default. Select a card action to continue the device's lifecycle." actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => refresh().catch(() => {})}>Refresh</Button>}>
      {error && <InlineError message={error} onRetry={() => refresh().catch(() => {})}/>} {loading ? <div className="device-grid"><div className="skeleton-card"/><div className="skeleton-card"/></div> : devices.length ? <div className="device-grid">{devices.map((device) => {
        const confirmed = device.identity_version === 'V2_PUBLIC_ID' && device.registration_status === 'CONFIRMED' && !device.read_only;
        const terminal = ['RECYCLED','EXPORTED','DISPUTED'].includes(String(device.status).toUpperCase());
        return <DeviceSummary key={device.public_id || device.device_id} device={device}><Button size="sm" variant="secondary" onClick={() => setModal({ type: 'transfer', device })} disabled={!confirmed || !device.stamp_valid || terminal}>Transfer</Button><Button size="sm" variant="ghost" onClick={() => setModal({ type: 'hardware', device })} disabled={!confirmed || terminal}>Hardware change</Button><Button size="sm" variant="ghost" onClick={() => setModal({ type: 'share', device })} disabled={!confirmed || terminal}>{device.stamp_valid ? 'Share for inspection' : 'Re-verify'}</Button><Button size="sm" variant="ghost" onClick={() => setModal({ type: 'dispute', device })} disabled={!confirmed || terminal}>Report issue</Button>{!confirmed && <small className="muted">Actions unlock after confirmed V2 registration.</small>}{confirmed && !device.stamp_valid && !terminal && <small className="muted">A current stamp is needed before transfer.</small>}</DeviceSummary>;
      })}</div> : <EmptyState icon={Laptop} title="No active devices yet" description={role === 'FIRST_BUYER' ? 'Once purchase proof is approved, register your first device.' : 'Devices transferred to your account will appear here.'} action={role === 'FIRST_BUYER' ? <Link className="button button-primary" to="/devices/new">Register device</Link> : null}/>}
      <div className="legacy-toggle"><button type="button" className="text-button" onClick={() => setShowLegacy((v) => !v)}>{showLegacy ? 'Hide V1 legacy test records' : 'Show V1 legacy test records'}</button></div>{showLegacy && <div className="legacy-list">{legacyRows.length ? legacyRows.map((d) => <div className="work-row" key={d.public_id || d.device_id}><div className="work-row-icon"><History size={18}/></div><div className="work-row-main"><strong>{d.brand || 'Legacy device'}</strong><small className="mono">{d.public_id || d.device_id}</small></div><Badge>Read-only V1</Badge><CopyButton value={d.public_id || d.device_id} onCopy={toast}/></div>) : <p className="muted">No V1 legacy records found for this account.</p>}</div>}
    </Panel><IncomingTransfersPanel onRefresh={() => refresh().catch(() => {})}/></div>
    <aside className="workspace-side"><Panel title="Your TraceLoop ID" description="Share this identifier in person when another participant needs to address a handoff or verification request."><div className="identifier-card vertical"><div><small>Account ID</small><strong className="mono">{profile.user_id}</strong></div><CopyButton value={profile.user_id} onCopy={toast}/></div><p className="muted compact">This is your account identifier, not a device Passport ID.</p></Panel><Panel title="My dispute reports" description="Reports you submit are reviewed before any on-chain freeze.">{disputesError && <InlineError message={disputesError} onRetry={loadDisputes}/ >}{myDisputes.length ? <div className="list-stack">{myDisputes.map((d) => <div className="simple-row" key={d.dispute_id}><div><strong>{d.brand_name || 'Device issue'}</strong><small className="mono">{d.device_id}</small><small>{displayState(d.dispute_type)} · {formatDate(d.created_at)}</small></div><StateBadge value={d.status}/></div>)}</div> : <EmptyState icon={ShieldCheck} title="No reports yet" description="Reported issues will appear here."/>}</Panel><Panel title="Quick links"><div className="quick-link-list"><Link to="/track"><Search size={16}/> Track a passport <ArrowRight size={15}/></Link><Link to="/transfer"><ArrowLeftRight size={16}/> Transfers <ArrowRight size={15}/></Link>{role === 'FIRST_BUYER' && <Link to="/devices/new"><Plus size={16}/> Register device <ArrowRight size={15}/></Link>}</div></Panel></aside></div>
    <TransferModal open={modal.type === 'transfer'} device={modal.device} onClose={() => setModal({ type: '', device: null })} onSuccess={() => refresh().catch(() => {})}/><HardwareChangeModal open={modal.type === 'hardware'} device={modal.device} onClose={() => setModal({ type: '', device: null })} onSuccess={() => refresh().catch(() => {})}/><ShareVerificationModal open={modal.type === 'share'} device={modal.device} onClose={() => setModal({ type: '', device: null })} onSuccess={() => {}}/><DisputeModal open={modal.type === 'dispute'} devices={devices} defaultDevice={modal.device} onClose={() => setModal({ type: '', device: null })} onSuccess={loadDisputes}/>
  </section>;
}

export function TransferPage() {
  const { profile } = useAuth();
  const { toast } = useToast();
  const { devices, loading, error, refresh } = useMyDevices(false);
  const [selectedId, setSelectedId] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const eligible = devices.filter((d) => d.identity_version === 'V2_PUBLIC_ID' && d.registration_status === 'CONFIRMED' && !d.read_only && d.stamp_valid && !['DISPUTED','RECYCLED','EXPORTED'].includes(String(d.status).toUpperCase()));
  const selected = eligible.find((d) => (d.public_id || d.device_id) === selectedId) || null;
  useEffect(() => { if (!selectedId && eligible.length) setSelectedId(eligible[0].public_id || eligible[0].device_id); }, [eligible, selectedId]);
  return <section className="page-wrap"><PageIntro eyebrow="Ownership handoff" title="Transfers" description="Initiate a transfer with a recipient you have met, or manage incoming requests. Ownership changes only after acceptance and physical verification."/><div className="workspace-grid"><div className="workspace-primary"><Panel title="Start a transfer" description="Choose one of your devices, then paste the recipient's TraceLoop user ID.">{error && <InlineError message={error} onRetry={() => refresh().catch(() => {})}/ >}{loading ? <div className="skeleton-row"/> : eligible.length ? <div className="transfer-start"><DeviceOptions devices={eligible} value={selectedId} onChange={(e) => setSelectedId(e.target.value)} label="Device to transfer" help="Only registered, writable V2 devices with a valid stamp are selectable."/><Button onClick={() => setModalOpen(true)} disabled={!selected}>Continue <ArrowRight size={16}/></Button></div> : <EmptyState icon={ShieldCheck} title="No devices ready to transfer" description="Transfer requires confirmed V2 registration, a valid verification stamp, and a non-terminal device state."/>}</Panel><IncomingTransfersPanel onRefresh={() => refresh().catch(() => {})}/></div><aside className="workspace-side"><Panel title="How the handoff works"><ol className="step-list"><li className="step-complete"><span>1</span><div><strong>Initiate</strong><small>Seller enters the recipient ID.</small></div></li><li><span>2</span><div><strong>Accept</strong><small>Recipient accepts the handoff in their workspace.</small></div></li><li><span>3</span><div><strong>Physical inspection</strong><small>An authorized verifier completes the transfer on-chain.</small></div></li></ol></Panel><Panel title="Share your user ID"><div className="identifier-card"><div><small>Recipient can copy theirs from their workspace.</small><strong className="mono">{profile?.user_id}</strong></div><CopyButton value={profile?.user_id} onCopy={toast}/></div></Panel></aside></div><TransferModal open={modalOpen} device={selected} onClose={() => setModalOpen(false)} onSuccess={() => refresh().catch(() => {})}/></section>;
}

function VerifierWorkspace() {
  const { profile } = useAuth();
  const { toast } = useToast();
  const [requests, setRequests] = useState([]);
  const [requestDetails, setRequestDetails] = useState({});
  const [expanded, setExpanded] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [transferRows, setTransferRows] = useState([]);
  const [transferLoading, setTransferLoading] = useState(true);
  const [transferError, setTransferError] = useState('');
  const [rejectItem, setRejectItem] = useState(null);
  const [rejectReason, setRejectReason] = useState('');
  const [rejectBusy, setRejectBusy] = useState(false);
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await apiData('/verification/requests/assigned');
      setRequests((data.requests || []).filter((request) => request.status !== 'REJECTED'));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTransfers = useCallback(async () => {
    setTransferLoading(true);
    setTransferError('');
    try {
      const data = await apiData('/transfers/pending-completion');
      setTransferRows((data.transfers_to_complete || []).filter((row) => !row.is_legacy_test && !row.read_only));
    } catch (err) {
      setTransferError(err.message);
    } finally {
      setTransferLoading(false);
    }
  }, []);

  useEffect(() => { load(); loadTransfers(); }, [load, loadTransfers]);

  const openDetails = async (request) => {
    if (expanded === request.verification_id) {
      setExpanded('');
      return;
    }
    setExpanded(request.verification_id);
    if (requestDetails[request.verification_id]) return;
    try {
      const details = await apiData(`/verification/requests/${encodeURIComponent(request.verification_id)}`);
      setRequestDetails((old) => ({ ...old, [request.verification_id]: details }));
    } catch (err) {
      toast(err.message, 'error');
    }
  };

  const stamp = async (request) => {
    setBusy(true);
    setConfirm(null);
    try {
      const data = await apiData('/verification/stamp', { method: 'POST', body: JSON.stringify({ verification_id: request.verification_id }) });
      toast(data.message || 'Verification stamp request completed.');
      setRequestDetails((old) => ({ ...old, [request.verification_id]: { ...(old[request.verification_id] || {}), status: data.verification_status || 'STAMPED', stamped_at: new Date().toISOString() } }));
      await load();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const reject = async (event) => {
    event.preventDefault();
    if (!rejectItem) return;
    setRejectBusy(true);
    try {
      const data = await apiData(`/verification/requests/${encodeURIComponent(rejectItem.verification_id)}/reject`, { method: 'POST', body: JSON.stringify({ reason: rejectReason.trim() }) });
      toast(data.message || 'Inspection request rejected.');
      setRejectItem(null);
      setRejectReason('');
      await load();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setRejectBusy(false);
    }
  };

  const completeTransfer = async () => {
    if (!confirm) return;
    const row = confirm;
    setConfirm(null);
    setBusy(true);
    try {
      const data = await apiData(`/transfers/${encodeURIComponent(row.transfer_id)}/complete`, { method: 'POST' });
      toast(data.message || 'Transfer completion submitted.');
      await loadTransfers();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const nextInspectionRequest = requests.find((request) => ['PENDING', 'IN_PROGRESS'].includes(String(request.status || '').toUpperCase()));
  const focusQueue = (id) => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  return <section className="page-wrap">
    <PageIntro eyebrow="Authorized service centre" title="Inspection workspace" description="Work only from passports owners explicitly shared with this service centre. Verify the physical device before issuing an on-chain stamp." actions={<Button variant="secondary" icon={RefreshCw} onClick={() => { load(); loadTransfers(); }}>Refresh queues</Button>}/>
    <div className="identifier-card wide"><div><small>Your service-centre TraceLoop user ID</small><strong className="mono">{profile?.user_id}</strong><p>Owners enter or scan this identifier when sharing a passport for inspection.</p></div><CopyButton value={profile?.user_id} onCopy={toast}/></div>

    {loading || transferLoading
      ? <WorkflowNextStep tone="amber" icon={ClipboardCheck} current="Work queues are loading" next="Wait for both queues to finish" detail="The page will show the next action when the latest inspection and transfer states are available."/>
      : error || transferError
        ? <WorkflowNextStep tone="amber" icon={CircleAlert} current="One or more queues failed to load" next="Refresh the queues" detail="Do not assume there is no pending work until both queues load successfully." recovery="If refresh still fails, wait and retry once the service responds; do not act on stale queue data." actions={<Button size="sm" variant="secondary" onClick={() => { load(); loadTransfers(); }}>Refresh queues <RefreshCw size={15}/></Button>}/>
        : transferRows.length
          ? <WorkflowNextStep icon={ClipboardCheck} current={`${transferRows.length} accepted handoff${transferRows.length === 1 ? '' : 's'} awaiting completion`} next="Inspect, then complete the handoff" detail="Compare the physical device and transfer details. Once the real handoff is confirmed, complete it to update ownership and the ledger." recovery="If completion times out or the status is unclear, refresh the transfer and reconcile its result before retrying." actions={<Button size="sm" onClick={() => focusQueue('verifier-handoff-queue')}>Review handoffs <ArrowRight size={15}/></Button>}/>
          : nextInspectionRequest
            ? <WorkflowNextStep icon={ClipboardCheck} current="An inspection request is waiting" next="Open it and compare the device" detail="Check the physical laptop against the owner-shared details. Issue the stamp only if the inspection supports it; otherwise reject with a clear reason." recovery="If stamping fails or times out, check the request and transaction status; reconcile an uncertain attempt before issuing another stamp." actions={<Button size="sm" onClick={() => { if (expanded !== nextInspectionRequest.verification_id) openDetails(nextInspectionRequest); focusQueue('verifier-inspection-queue'); }}>Open inspection request <ArrowRight size={15}/></Button>}/>
            : <WorkflowNextStep icon={CheckCircle2} current="No pending action in the loaded queues" next="Refresh when the state changes" detail="New requests appear after a passport is shared; accepted transfers appear here when physical completion is needed." recovery="If an expected item is missing, refresh both queues and confirm they loaded successfully before assuming there is no work." actions={<Button size="sm" variant="secondary" onClick={() => { load(); loadTransfers(); }}>Refresh queues <RefreshCw size={15}/></Button>}/>}


    <div id="verifier-inspection-queue" className="workflow-stage-anchor">
      <Panel title="Owner-shared inspection requests" description="Open a request to review the shared device details. The backend controls access and stamp eligibility.">
        {error && <InlineError message={error} onRetry={load}/>}
        {loading ? <div className="skeleton-stack"><div className="skeleton-row"/><div className="skeleton-row"/></div> : requests.length ? <div className="list-stack">{requests.map((request) => {
          const details = requestDetails[request.verification_id] || {};
          const requestStatus = String(details.status || request.status || '').toUpperCase();
          return <article className="queue-card" key={request.verification_id}>
            <div className="queue-card-head"><span className="work-row-icon"><ShieldCheck size={20}/></span><div className="work-row-main"><strong>{request.brand || 'Device'} inspection</strong><small className="mono">{request.public_id}</small><small>Shared {formatDate(request.created_at)}</small></div><StateBadge value={requestStatus}/></div>
            <div className="queue-actions"><Button size="sm" variant="secondary" onClick={() => openDetails(request)}>{expanded === request.verification_id ? 'Hide details' : 'Inspect request'}</Button>{['PENDING', 'IN_PROGRESS'].includes(requestStatus) && <Button size="sm" onClick={() => setConfirm(request)}><ShieldCheck size={15}/> Issue stamp</Button>}<Button size="sm" variant="ghost" onClick={() => { setRejectItem(request); setRejectReason(''); }} disabled={requestStatus !== 'PENDING'}>Reject request</Button></div>
            {expanded === request.verification_id && <div className="request-detail"><div className="detail-grid"><div><small>Passport ID</small><strong className="mono">{request.public_id}</strong></div><div><small>Serial number</small><strong>{details.serial_raw || 'Not available for this request'}</strong></div><div><small>Current configuration</small><strong>{details.current_config ? Object.entries(details.current_config).map(([key, value]) => `${displayState(key)}: ${value}`).join(' · ') : 'Not available'}</strong></div><div><small>Manufacturer comparison</small><strong>{details.manufacturer_match === true ? 'Match recorded' : details.manufacturer_match === false ? 'Differences recorded' : 'No baseline comparison returned'}</strong></div></div>{details.reconciliation_required && <Notice kind="warning">A prior stamping attempt needs reconciliation. Do not blindly retry the on-chain action.</Notice>}<p className="muted compact">Compare the physical laptop against the shared configuration. This screen does not claim a manufacturer baseline unless the backend has one.</p></div>}
          </article>;
        })}</div> : <EmptyState icon={ClipboardCheck} title="No assigned inspection requests" description="A request appears here after the owner completes the passport-sharing step. A public Passport ID alone does not grant inspection access."/>}
      </Panel>
    </div>

    <div id="verifier-handoff-queue" className="workflow-stage-anchor">
      <Panel title="Ownership transfers awaiting physical completion" description="Recipients have accepted these transfers. Complete only after the physical device and parties have been checked." actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={loadTransfers}>Refresh</Button>}>
        {transferError && <InlineError message={transferError} onRetry={loadTransfers}/>}
        {transferRows.length ? <div className="list-stack">{transferRows.map((row) => <div className="work-row" key={row.transfer_id}><span className="work-row-icon"><ArrowLeftRight size={19}/></span><div className="work-row-main"><strong>{row.brand_name || 'Device handoff'}</strong><small className="mono">{row.device_id}</small><small>Initiated {formatDate(row.initiated_at)}</small></div><CopyButton value={row.device_id} onCopy={toast}/><Button size="sm" onClick={() => setConfirm({ ...row, isTransferCompletion: true })}>Complete handoff</Button></div>)}</div> : <EmptyState icon={ArrowLeftRight} title="No transfers awaiting completion" description="Accepted ownership handoffs will appear here for physical inspection."/>}
      </Panel>
    </div>

    <Modal open={!!rejectItem} title="Reject inspection request" description="Use a clear reason so the owner can understand the next step." onClose={() => setRejectItem(null)}><form className="form-stack" onSubmit={reject}><TextArea label="Reason for rejection" value={rejectReason} onChange={(event) => setRejectReason(event.target.value)} required minLength={1} maxLength={500} rows={3} placeholder="e.g. Serial number does not match the device"/><div className="modal-actions"><Button variant="secondary" type="button" onClick={() => setRejectItem(null)}>Cancel</Button><Button variant="danger" type="submit" loading={rejectBusy}>Reject request</Button></div></form></Modal>
    <ConfirmModal open={!!confirm} title={confirm?.isTransferCompletion ? 'Complete ownership handoff?' : 'Issue verification stamp?'} description={confirm?.isTransferCompletion ? 'This finalizes the accepted transfer through the existing verifier workflow and writes to Algorand. Confirm that the physical handoff has occurred.' : 'This writes a verification action to Algorand. Proceed only after physically inspecting the device and confirming the owner-shared details.'} actionLabel={confirm?.isTransferCompletion ? 'Complete handoff' : 'Issue stamp on-chain'} onCancel={() => setConfirm(null)} onConfirm={confirm?.isTransferCompletion ? completeTransfer : () => stamp(confirm)} busy={busy}/>
  </section>;
}

export function RecyclingPage() {
  const { toast } = useToast();
  const [workflow, setWorkflow] = useState({ incoming_handoffs: [], awaiting_verifier: [], awaiting_receipt: [], ready_for_recycling: [], reconciliation_required: [], counts: {} });
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [confirm, setConfirm] = useState(null); const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { setLoading(true); setError(''); try { setWorkflow(await apiData('/recycling/workflow')); } catch (e) { setError(e.message); } finally { setLoading(false); } }, []);
  useEffect(() => { load(); }, [load]);
  const runAction = async () => { if (!confirm) return; const action = confirm; setConfirm(null); setBusy(true); try { let data; if (action.type === 'accept' || action.type === 'cancel') data = await apiData(`/transfers/${encodeURIComponent(action.row.transfer_id)}/${action.type}`, { method: 'POST' }); else if (action.type === 'receive') data = await apiData('/recycling/receive', { method: 'POST', body: JSON.stringify({ device_id: action.row.device_id }) }); else data = await apiData('/recycling/complete', { method: 'POST', body: JSON.stringify({ device_id: action.row.device_id }) }); toast(data.message || 'Workflow action complete.'); await load(); } catch (e) { toast(e.message, 'error'); } finally { setBusy(false); } };
  const renderRows = (rows, stage) => rows?.length ? <div className="device-grid">{rows.map((row) => <article className="stage-card panel-card" key={row.transfer_id || row.device_id}><div className="device-card-top"><span className="device-brand-mark"><Recycle size={19}/></span><StateBadge value={row.recycling_status || row.status || row.device_status || stage}/><span className="grow"/><CopyButton value={row.device_id} onCopy={toast}/></div><h3>{row.brand_name || 'Device'}</h3><p className="mono device-id">{row.device_id}</p>{row.initiated_at && <p className="muted compact">Handoff {formatDate(row.initiated_at)}</p>}{row.current_config && Object.keys(row.current_config).length > 0 && <div className="spec-list">{Object.entries(row.current_config).slice(0,3).map(([k,v])=><div className="spec-row" key={k}><span>{displayState(k)}</span><strong>{String(v)}</strong></div>)}</div>}
      {stage === 'incoming' && <div className="card-actions"><Button size="sm" onClick={() => setConfirm({ type: 'accept', row, title: 'Accept recycling handoff?', description: 'Accepting moves the handoff to the service-centre completion stage. The device is not yet owned by your facility.' })}>Accept handoff</Button><Button size="sm" variant="secondary" onClick={() => setConfirm({ type: 'cancel', row, title: 'Cancel this handoff?', description: 'Cancelling ends the pending handoff. The backend will validate that you are an eligible transfer participant.' })}>Cancel</Button></div>}
      {stage === 'awaiting-verifier' && <Notice kind="info">Recipient accepted this handoff. An authorized service centre must physically inspect and complete the ownership transfer before the device can be received into this facility.</Notice>}
      {stage === 'receipt' && <div className="card-actions"><Button size="sm" onClick={() => setConfirm({ type: 'receive', row, title: 'Acknowledge physical receipt?', description: 'Confirm only after the device has physically arrived at your facility. This records receipt; it does not complete recycling.' })}>Acknowledge receipt</Button></div>}
      {stage === 'recycle' && <div className="card-actions"><Button size="sm" variant="danger" onClick={() => setConfirm({ type: 'complete', row, title: 'Complete recycling on-chain?', description: 'This is an irreversible ledger action. Confirm that physical recycling was completed under your process. TraceLoop will create a recycling record; it does not issue a government CPCB certificate.' })}><Recycle size={15}/> Complete recycling</Button></div>}
      {stage === 'reconcile' && <Notice kind="warning">A previous ledger attempt needs reconciliation. Do not resubmit automatically.{row.chain_tx_id && <div className="compact mono">Transaction: {row.chain_tx_id}</div>}</Notice>}
    </article>)}</div> : <EmptyState icon={Recycle} title={stage === 'incoming' ? 'No incoming handoffs' : stage === 'receipt' ? 'No devices awaiting receipt' : stage === 'recycle' ? 'No devices ready for recycling' : 'No cases need reconciliation'} description={stage === 'incoming' ? 'New handoffs to this facility appear here.' : stage === 'receipt' ? 'Accepted and physically completed transfers will appear here.' : stage === 'recycle' ? 'Acknowledge physical receipt before completing recycling.' : 'Any uncertain ledger attempt will be shown for manual recovery.'}/>;
  const recyclerStep = error
    ? { current: 'The latest queue could not be loaded', next: 'Refresh the pipeline', detail: 'Do not act on a stale queue. Refresh to retrieve the current device and ledger states.', recovery: 'If refresh still fails, wait and retry after the service recovers. Do not make decisions from stale queue data.', tone: 'amber' }
    : workflow.reconciliation_required?.length
      ? { current: 'A previous ledger attempt is uncertain', next: 'Resolve reconciliation first', detail: 'Open the reconciliation queue and inspect the existing transaction. Do not repeat the same on-chain action.', recovery: 'If the outcome is still unknown, keep the item in reconciliation and retry the check later; do not submit a duplicate transaction.', tone: 'amber' }
      : workflow.incoming_handoffs?.length
        ? { current: 'A handoff is waiting for your response', next: 'Review and accept the handoff', detail: 'Once accepted, the next stage is physical ownership-transfer completion. Acceptance alone does not move ownership.', recovery: 'If accept/cancel fails, refresh the queue and act only if the transfer is still in the same state.', tone: 'green' }
        : workflow.awaiting_verifier?.length
          ? { current: 'The handoff was accepted', next: 'Wait for transfer completion', detail: 'After the physical handoff is recorded, the device moves to the facility-receipt queue. Refresh to check for the state change.', recovery: 'If the queue does not update, refresh the pipeline and review the transfer status; do not acknowledge facility receipt before the transfer is recorded.', tone: 'green' }
          : workflow.awaiting_receipt?.length
            ? { current: 'Ownership transfer is complete', next: 'Acknowledge physical receipt', detail: 'Confirm receipt only after the device arrives at the facility. This step does not yet record recycling completion.', recovery: 'If receipt submission fails or times out, refresh the record before retrying and confirm the receipt state is still pending.', tone: 'green' }
            : workflow.ready_for_recycling?.length
              ? { current: 'Facility receipt is recorded', next: 'Complete recycling after processing', detail: 'After the physical recycling process is complete, record the terminal lifecycle event. This on-chain action cannot be undone through the UI.', recovery: 'If submission fails or its result is unclear, reconcile the existing attempt before retrying the irreversible action.', tone: 'green' }
              : { current: 'All lifecycle queues are clear', next: 'No action is due right now', detail: 'Refresh the pipeline when a new handoff arrives or another lifecycle state changes.', recovery: 'If an expected handoff does not appear, refresh the pipeline and check that the queue loaded successfully.', tone: 'green' };
  return <section className="page-wrap"><PageIntro eyebrow="Recycler operations" title="Recycling pipeline" description="One work queue for incoming handoffs, facility receipt, recycling completion, and cases requiring reconciliation." actions={<Button variant="secondary" icon={RefreshCw} onClick={load}>Refresh pipeline</Button>}/>{error && <Notice kind="error">{error}<button type="button" className="text-button" onClick={load}>Retry</button></Notice>}
    {!loading && <WorkflowNextStep tone={recyclerStep.tone} icon={Recycle} current={recyclerStep.current} next={recyclerStep.next} detail={recyclerStep.detail} recovery={recyclerStep.recovery}/>}
    <div className="stat-grid modern-stats"><StatCard icon={ArrowDownLeft} label="Incoming" value={workflow.counts?.incoming_handoffs ?? '—'} detail="Awaiting recipient response"/><StatCard icon={ShieldCheck} label="Awaiting verifier" value={workflow.counts?.awaiting_verifier ?? '—'} detail="Accepted; physical handoff pending" tone="blue"/><StatCard icon={PackageCheck} label="Awaiting receipt" value={workflow.counts?.awaiting_receipt ?? '—'} detail="Transferred to your facility" tone="blue"/><StatCard icon={Recycle} label="Ready to complete" value={workflow.counts?.ready_for_recycling ?? '—'} detail="Receipt acknowledged" tone="amber"/><StatCard icon={CircleAlert} label="Reconciliation" value={workflow.counts?.reconciliation_required ?? '—'} detail="No automatic retry permitted" tone="red"/></div>
    {loading ? <div className="skeleton-stack"><div className="skeleton-row"/><div className="skeleton-row"/><div className="skeleton-row"/></div> : <div className="pipeline-stack"><Panel title="1 · Incoming handoffs" description="Accepting moves the transfer to physical inspection. The verifier must complete ownership transfer before the device appears in your receipt queue.">{renderRows(workflow.incoming_handoffs, 'incoming')}</Panel><Panel title="Accepted · awaiting service-centre completion" description="Acceptance alone does not change ownership. This stage remains visible until an authorized verifier completes the physical handoff.">{renderRows(workflow.awaiting_verifier, 'awaiting-verifier')}</Panel><Panel title="2 · Awaiting facility receipt" description="Acknowledge only after physically receiving the device at this facility.">{renderRows(workflow.awaiting_receipt, 'receipt')}</Panel><Panel title="3 · Ready for recycling completion" description="The irreversible on-chain recycling transition is available only after physical receipt is recorded.">{renderRows(workflow.ready_for_recycling, 'recycle')}</Panel>{workflow.reconciliation_required?.length > 0 && <Panel title="Requires reconciliation" description="Do not retry these chain actions automatically.">{renderRows(workflow.reconciliation_required, 'reconcile')}</Panel>}</div>}
    <ConfirmModal open={!!confirm} title={confirm?.title || 'Confirm action'} description={confirm?.description} actionLabel={confirm?.type === 'complete' ? 'Complete recycling on-chain' : confirm?.type === 'receive' ? 'Confirm receipt' : confirm?.type === 'accept' ? 'Accept handoff' : 'Cancel handoff'} danger={confirm?.type === 'complete' || confirm?.type === 'cancel'} onCancel={() => setConfirm(null)} onConfirm={runAction} busy={busy}/>
  </section>;
}

function renderMarkdownSafe(text) {
  // Accept Markdown from the wording service and recover headings/bullets from
  // older responses where line breaks were collapsed into a single paragraph.
  const normalized = String(text || '')
    .replace(/\r\n?/g, '\n')
    .replace(/\\n/g, '\n')
    .replace(/\\(?=[*#])/g, '')
    .replace(/\\[ \t]*(?=\n|$)/g, '')
    .replace(/\s+(?=#{1,6}\s)/g, '\n')
    .replace(/\s+\*\s+(?=\S)/g, '\n* ')
    .replace(/\s+([•-])\s+(?=\S)/g, '\n$1 ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  const inline = (value) => value.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, idx) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={idx}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`')) return <code key={idx}>{part.slice(1, -1)}</code>;
    return part;
  });
  return normalized.split('\n').map((raw, index) => {
    const line = raw.trim();
    if (!line) return <div key={`sp-${index}`} className="markdown-spacer"/>;
    const heading = line.match(/^#{1,6}\s+(.+)$/);
    if (heading) return <h4 key={index} className="markdown-heading">{inline(heading[1])}</h4>;
    const ordered = line.match(/^(\d+)[.)]\s+(.+)$/);
    if (ordered) return <div key={index} className="markdown-bullet markdown-ordered"><span>{ordered[1]}.</span><div>{inline(ordered[2])}</div></div>;
    const bullet = line.match(/^(?:[*•-])\s*(.+)$/);
    if (bullet) return <div key={index} className="markdown-bullet"><span>•</span><div>{inline(bullet[1])}</div></div>;
    return <p key={index} className="markdown-paragraph">{inline(line)}</p>;
  });
}

function reconciliationGuidance(status) {
  const state = String(status || 'SOURCE_UNAVAILABLE').toUpperCase();
  const guidance = {
    MATCH: {
      title: 'Comparable records match',
      body: 'The comparable database and chain fields agree. This confirms consistency between the returned records; it does not independently prove that a physical inspection, sale, or recycling event occurred.',
      kind: 'match',
    },
    CONFLICT: {
      title: 'Comparable records conflict',
      body: 'At least one comparable field differs between sources. Treat this passport as unresolved until the conflicting field is understood and corrected through the existing workflow.',
      kind: 'conflict',
    },
    PENDING_SYNC: {
      title: 'The sources are still syncing',
      body: 'The returned data is not ready for a final comparison. A recent action may not yet be reflected in both sources.',
      kind: 'pending',
    },
    CHAIN_ONLY: {
      title: 'The chain record has no comparable local record',
      body: 'A chain-side entry was found, but the matching local record was not returned. A ledger entry alone does not prove the underlying physical event.',
      kind: 'partial',
    },
    OFFCHAIN_ONLY: {
      title: 'The local record has no matching chain record',
      body: 'The local database returned a record, but no comparable chain record was found. The transaction outcome needs to be checked before any retry.',
      kind: 'partial',
    },
    PARTIAL: {
      title: 'Only part of the record could be checked',
      body: 'Some sources or fields were unavailable or not comparable, so the check cannot establish a complete match.',
      kind: 'partial',
    },
    SOURCE_UNAVAILABLE: {
      title: 'Reconciliation is unavailable',
      body: 'One or more required data sources could not be read. This result is inconclusive—not a pass and not a confirmed conflict.',
      kind: 'unavailable',
    },
  };
  return guidance[state] || guidance.SOURCE_UNAVAILABLE;
}

function passportNextStep(device, reconciliationStatus) {
  const reconciliation = String(reconciliationStatus || 'SOURCE_UNAVAILABLE').toUpperCase();
  const state = String(device?.status || 'UNKNOWN').toUpperCase();
  const registration = String(device?.registration_status || device?.chain_status || 'UNKNOWN').toUpperCase();

  if (reconciliation === 'CONFLICT') return {
    kind: 'conflict', current: 'Reconciliation found a mismatch', title: 'Review the field-level checks first',
    text: 'Find the field marked Conflict and correct the underlying record through its existing workflow. Pause any new lifecycle action until the discrepancy is resolved.',
    recovery: 'Refresh the passport and run reconciliation again after the underlying record is corrected. If the mismatch remains, keep the device on hold rather than guessing which source is right.',
  };
  if (reconciliation === 'PENDING_SYNC') return {
    kind: 'pending', current: 'The latest action may still be syncing', title: 'Wait, refresh, then run the check again',
    text: 'Give the source mirror time to update, refresh this passport, and rerun reconciliation. Do not repeat the same irreversible action while its outcome is pending.',
    recovery: 'If it remains pending after refresh, keep the existing attempt and retry the reconciliation check later; do not submit a duplicate action.',
  };
  if (reconciliation === 'CHAIN_ONLY') return {
    kind: 'partial', current: 'A chain event exists without a local counterpart', title: 'Resolve the missing local record',
    text: 'Review the transaction reference and source-coverage notes. The chain event exists, but the current local record does not show a comparable counterpart.',
    recovery: 'Use the existing reconciliation recovery path to resolve the local record. Do not repeat the on-chain action just to recreate a database entry.',
  };
  if (reconciliation === 'OFFCHAIN_ONLY') return {
    kind: 'partial', current: 'A local record exists without a matching chain event', title: 'Confirm the transaction outcome before retrying',
    text: 'Check the registration or transaction status and available error details. A local record alone does not confirm that the chain action completed.',
    recovery: 'Retry only when the record confirms no transaction was submitted. If submission may have occurred, reconcile the existing attempt first.',
  };
  if (reconciliation === 'PARTIAL') return {
    kind: 'partial', current: 'Some source data is missing or not comparable', title: 'Review source coverage, then rerun the check',
    text: 'Use the source-coverage panel to identify what was unavailable. A partial comparison is not a full match.',
    recovery: 'Refresh and rerun reconciliation when the missing source returns usable data. Do not treat unknown fields as verified.',
  };
  if (reconciliation === 'SOURCE_UNAVAILABLE') return {
    kind: 'unavailable', current: 'The comparison could not finish', title: 'Retry reconciliation when the source is available',
    text: 'Refresh the passport and run the check again. Until it completes, treat the result as unknown and avoid decisions that depend on a confirmed match.',
    recovery: 'If repeated attempts fail, wait for the source to recover and retry later; do not interpret an unavailable result as a match or a conflict.',
  };

  if (registration === 'RECONCILIATION_REQUIRED') return {
    kind: 'pending', current: 'The registration outcome is uncertain', title: 'Check the existing transaction before any retry',
    text: 'Use the transaction reference and registration status to resolve this attempt. Do not submit another registration while the first outcome is uncertain.',
    recovery: 'If the outcome cannot be established yet, leave the attempt unresolved and rerun reconciliation after the source is available.',
  };
  if (registration === 'FAILED') return {
    kind: 'conflict', current: 'Registration did not complete', title: 'Review the failed attempt before retrying',
    text: 'Read the registration status and error details to understand where the step stopped.',
    recovery: 'Retry only if the record confirms no transaction was submitted. Otherwise, reconcile the existing attempt first.',
  };
  if (['PENDING', 'SUBMISSION_STARTED'].includes(registration)) return {
    kind: 'pending', current: 'Registration is in progress', title: 'Wait for registration confirmation',
    text: 'Refresh the passport after a short wait. Verification comes next only after registration is confirmed; transfers stay unavailable until a valid verification stamp exists.',
    recovery: 'If registration remains pending, run reconciliation before trying to register the same device again.',
  };
  if (state === 'DISPUTED') return {
    kind: 'conflict', current: 'The device is on hold', title: 'Wait for the dispute state to be resolved',
    text: 'Do not start a transfer while the passport is disputed. After the status changes, review the new state and check whether the verification stamp remains valid.',
    recovery: 'Refresh the passport to check the current dispute status. Keep transfer actions paused while the record remains disputed.',
  };
  if (state === 'RECYCLED' || state === 'EXPORTED') return {
    kind: 'match', current: 'The lifecycle has reached a terminal state', title: 'Review the final lifecycle record',
    text: 'Review the final lifecycle event and available evidence references. The device is not eligible for another ownership transfer.',
    recovery: 'If the final record is incomplete or inconsistent, review its sources and reconciliation result; do not initiate another lifecycle transition.',
  };
  if (state === 'TRANSFERRED') return {
    kind: device?.stamp_valid ? 'match' : 'pending', current: 'The ownership transfer is recorded', title: device?.stamp_valid ? 'Review the updated passport before another handoff' : 'Complete verification before another transfer',
    text: device?.stamp_valid ? 'Confirm that the current passport and ownership state are up to date. A new transfer is only needed when another handoff is intended.' : 'The transfer is recorded, but the current passport does not show a valid verification stamp. Re-verification must be completed before another transfer.',
    recovery: 'If the transfer result is uncertain, refresh and reconcile the existing transfer before starting another. If the stamp is invalid, follow the verification workflow first.',
  };
  if (!device?.stamp_valid) return {
    kind: 'pending', current: 'No valid verification stamp is shown', title: 'Complete device verification',
    text: 'If you own this device, the next in-app step is to submit the passport for inspection. Once verification is recorded, refresh this page and confirm the stamp is valid before a transfer.',
    recovery: 'If verification is rejected, use the recorded reason to correct the relevant device details and resubmit. If the stamp result is unclear, run reconciliation before retrying.',
  };
  return {
    kind: 'match', current: 'The comparable records match', title: 'Review the record, then continue only when needed',
    text: 'No corrective action is indicated by this check. Review the lifecycle history and evidence references; start a transfer only when a handoff is actually needed.',
    recovery: 'If a later action fails or its result is uncertain, refresh the passport and reconcile before retrying an irreversible step.',
  };
}

export function TrackPage() {
  const { toast } = useToast();
  const location = useLocation();
  const navigate = useNavigate();
  const [query, setQuery] = useState(new URLSearchParams(location.search).get('id') || '');
  const [device, setDevice] = useState(null);
  const [intelligence, setIntelligence] = useState(null);
  const [loading, setLoading] = useState(false);
  const [explainLoading, setExplainLoading] = useState(false);
  const [error, setError] = useState('');
  const [explainError, setExplainError] = useState('');
  const [demoConfigMissing, setDemoConfigMissing] = useState(false);
  const lastLoadedId = useRef('');

  const loadExplanationFor = useCallback(async (passportId) => {
    if (!passportId) return;
    setExplainLoading(true);
    setExplainError('');
    try {
      const result = await apiData(`/passport-intelligence/${encodeURIComponent(passportId)}`, { auth: false });
      setIntelligence(result);
    } catch (err) {
      setExplainError(err.message || 'Reconciliation could not be completed.');
    } finally {
      setExplainLoading(false);
    }
  }, []);

  const loadPassportById = useCallback(async (rawId, { autoExplain = false } = {}) => {
    const id = String(rawId || '').trim().toUpperCase().replace(/[^A-Z0-9-]/g, '');
    if (!id) {
      setError('Enter a public Passport ID.');
      return;
    }
    lastLoadedId.current = id;
    setQuery(id);
    setLoading(true);
    setError('');
    setDevice(null);
    setIntelligence(null);
    setExplainError('');
    setDemoConfigMissing(false);
    try {
      const result = await apiData(`/devices/public/${encodeURIComponent(id)}`, { auth: false });
      setDevice(result);
      if (autoExplain) await loadExplanationFor(id);
    } catch (err) {
      setError(err.message || 'This passport could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [loadExplanationFor]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const demoRequested = params.get('demo') === '1';
    const linkedId = params.get('id');
    if (demoRequested) {
      if (!DEMO_PASSPORT_ID) {
        setDemoConfigMissing(true);
        setError('The sample passport has not been configured for this deployment.');
        setDevice(null);
        setIntelligence(null);
        return;
      }
      if (lastLoadedId.current !== DEMO_PASSPORT_ID) {
        loadPassportById(DEMO_PASSPORT_ID, { autoExplain: true });
      }
      return;
    }
    setDemoConfigMissing(false);
    if (linkedId && lastLoadedId.current !== linkedId.trim().toUpperCase()) {
      loadPassportById(linkedId);
    } else if (!linkedId && !demoRequested) {
      lastLoadedId.current = '';
      setDevice(null);
      setIntelligence(null);
      setError('');
    }
  }, [location.search, loadPassportById]);

  const loadPassport = (event) => {
    event?.preventDefault();
    const id = query.trim().toUpperCase().replace(/[^A-Z0-9-]/g, '');
    if (!id) {
      setError('Enter a public Passport ID.');
      return;
    }
    const params = new URLSearchParams(location.search);
    if (params.get('id')?.trim().toUpperCase() === id && params.get('demo') !== '1') {
      loadPassportById(id);
    } else {
      navigate(`/track?id=${encodeURIComponent(id)}`, { replace: true });
    }
  };

  const events = Array.isArray(device?.events) ? device.events : [];
  const history = Array.isArray(device?.history) ? device.history : [];
  const isLegacy = Boolean(device?.is_legacy_test || device?.identity_version === 'V1_LEGACY');
  const recon = intelligence?.reconciliation || {};
  const sourceData = intelligence?.sources || {};
  const reconStatus = String(recon.status || 'SOURCE_UNAVAILABLE').toUpperCase();
  const reconHelp = reconciliationGuidance(reconStatus);
  const publicId = device?.public_id || device?.device_id || query;
  const nextStep = intelligence ? passportNextStep(device, reconStatus) : null;

  return <section className="page-wrap track-page">
    <PageIntro eyebrow="Public device passport" title="Trace a device" description="Review its lifecycle record, compare available sources, and see exactly what the evidence does—and does not—establish." />
    {demoConfigMissing && <Notice kind="warning">The one-click sample passport is not configured for this Vercel deployment. Add <code>VITE_DEMO_PASSPORT_ID</code> as an environment variable with a real, non-sensitive public Passport ID, then redeploy. No sample ID is fabricated by the frontend.</Notice>}
    <Panel title="Find a passport" description="Enter the public Passport ID printed or shared for the device. No account is required to inspect public records.">
      <form className="passport-search-form" onSubmit={loadPassport}>
        <TextInput label="Public Passport ID" value={query} onChange={(event) => setQuery(event.target.value.toUpperCase())} maxLength={40} required placeholder="e.g. TL-DELL-…" autoComplete="off" />
        <Button type="submit" loading={loading}>Find passport <Search size={16} /></Button>
      </form>
      {error && !demoConfigMissing && <Notice kind="error">{error}</Notice>}
      {loading && <div className="skeleton-stack"><div className="skeleton-row"/><div className="skeleton-row"/><div className="skeleton-card"/></div>}
    </Panel>

    {device && <div className="track-results">
      <Panel className="passport-overview-panel" title={device.brand_name || device.brand || 'Device passport'} description={`Public Passport ID · ${publicId}`} actions={<CopyButton value={publicId} onCopy={toast} />}>
        <div className="passport-state-strip">
          <div><span className="eyebrow">Current lifecycle state</span><StateBadge value={device.status || 'UNKNOWN'}/></div>
          <div><span className="eyebrow">Ledger registration</span><StateBadge value={device.registration_status || device.chain_status || 'UNKNOWN'}/></div>
          <div><span className="eyebrow">Verification stamp</span><StateBadge value={device.stamp_valid ? 'VALID' : 'NOT_VALID'}/></div>
        </div>
        <div className="passport-overview-meta"><span><History size={15}/> Registered {formatDate(device.registered_at)}</span><span><ShieldCheck size={15}/> Public view · private ownership data is not displayed</span></div>
        {isLegacy && <Notice kind="warning">Read-only historical V1 passport. Mutations are disabled for this legacy record.</Notice>}
      </Panel>

      <Panel className="reconciliation-panel" title="Reconciliation & explanation" description="The result compares available database and chain records using fixed backend rules. The explanation describes the returned evidence; it does not independently verify a physical event." actions={<Button loading={explainLoading} onClick={() => loadExplanationFor(publicId)}>{intelligence ? 'Run reconciliation again' : 'Run reconciliation & explain'} <ArrowRight size={15}/></Button>}>
        {explainError && <Notice kind="error">{explainError}</Notice>}
        {explainLoading && <div className="skeleton-stack"><div className="skeleton-row"/><div className="skeleton-row"/></div>}
        {!intelligence && !explainLoading && <EmptyState icon={ShieldCheck} title="Check whether the available records agree" description="This check compares only the source fields currently available for this passport. Run it to see the outcome, field-level checks, event history, and any limitations." action={<Button onClick={() => loadExplanationFor(publicId)}>Run reconciliation & explain <ArrowRight size={15}/></Button>}/>}
        {intelligence && <div className="intelligence-results">
          <div className={`reconciliation-banner reconciliation-banner-${reconHelp.kind}`}>
            <div className="reconciliation-banner-status"><StateBadge value={reconStatus}/></div>
            <div className="reconciliation-banner-copy"><strong>{recon.headline || reconHelp.title}</strong><p>{reconHelp.body}</p></div>
          </div>
          <div className={`next-action-callout next-action-${nextStep.kind}`}><div className="next-action-icon"><CircleHelp size={19}/></div><div><span className="eyebrow">Current step · {nextStep.current}</span><strong>Next: {nextStep.title}</strong><p>{nextStep.text}</p>{nextStep.recovery && <p className="next-action-recovery"><strong>If it stalls or fails:</strong> {nextStep.recovery}</p>}</div></div>
          <section className="explanation-box explanation-box-formatted"><div className="explanation-heading"><span className="eyebrow">Readable passport summary</span><span className="generated-label">{intelligence.explanation?.generated === false ? 'Rule-based fallback' : intelligence.explanation?.generated === true ? 'AI wording · grounded in returned facts' : 'Summary of returned facts'}</span></div><div className="markdown-content">{renderMarkdownSafe(intelligence.explanation?.text || intelligence.summary || 'No explanation was returned.')}</div></section>
          <section className="reconciliation-detail-section"><div className="reconciliation-section-heading"><div><h3>Field-level checks</h3><p>Only comparable fields can return a match or conflict.</p></div><Badge>{(recon.checks || []).length} checks</Badge></div>
            {(recon.checks || []).length ? <div className="checks-grid">{recon.checks.map((check) => <div className="check-card" key={check.field}><div><strong>{displayState(check.field)}</strong><p>{check.explanation}</p></div><StateBadge value={check.result}/></div>)}</div> : <EmptyState title="No comparable fields returned" description="Review source coverage and retry if the data source is temporarily unavailable."/>}
          </section>
          <section className="reconciliation-detail-section"><div className="reconciliation-section-heading"><div><h3>Independent chain history</h3><p>Events returned by the chain-history query—not claims about physical events.</p></div><Badge>{(intelligence.timeline || []).length} events</Badge></div>
            {(intelligence.timeline || []).length ? <div className="timeline-list">{intelligence.timeline.map((event, index) => <div className="timeline-item" key={`${event.event_type}-${event.round}-${index}`}><span className="timeline-dot"/><div className="timeline-copy"><strong>{displayState(event.event_type)}</strong><p>{event.description}</p><small>{formatDate(event.timestamp)}{event.round != null ? ` · round ${event.round}` : ''}</small></div>{event.transaction_ref && <a className="external-link" href={`https://lora.algokit.io/testnet/transaction/${encodeURIComponent(event.transaction_ref)}`} target="_blank" rel="noreferrer">View transaction <ArrowRight size={13}/></a>}</div>)}</div> : <EmptyState icon={History} title="No events in the returned history" description="The backend did not return public lifecycle events for this search window. This does not establish that no activity occurred."/>}
          </section>
          <section className="reconciliation-detail-section"><div className="reconciliation-section-heading"><div><h3>Source coverage</h3><p>These labels help explain why some comparisons may be incomplete.</p></div></div><div className="source-grid">{[['Current chain state',sourceData.current_chain_state],['Independent chain history',sourceData.independent_chain_history],['Database record',sourceData.database_record],['Local event mirror',sourceData.database_event_mirror]].map(([label,value]) => <div key={label}><small>{label}</small><strong>{displayState(value || 'UNKNOWN')}</strong></div>)}</div>{(intelligence.limitations || []).length > 0 && <div className="limitations-box"><strong>Coverage notes and limitations</strong><ul>{intelligence.limitations.map((note,index) => <li key={index}>{note}</li>)}</ul></div>}<p className="muted compact">Checked {formatDate(intelligence.checked_at)}. A ledger entry proves that a record was submitted to the ledger; it does not, by itself, prove a physical inspection, ownership claim, or recycling event.</p></section>
        </div>}
      </Panel>

      <Panel className="passport-record-panel" title="Device record details" description="Read the public configuration and mirrored records after reviewing the reconciliation result.">
        <div className="passport-info-grid"><div><h3>Current configuration</h3>{Object.entries(device.current_config || {}).length ? <div className="spec-list">{Object.entries(device.current_config || {}).map(([key,value]) => <div key={key} className="spec-row"><span>{displayState(key)}</span><strong>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</strong></div>)}</div> : <p className="muted">No public configuration fields are available.</p>}</div><div><h3>Mirrored lifecycle history</h3>{history.length ? <div className="timeline-list">{history.map((row,index) => <div className="timeline-item" key={`${row.status || row.event_type}-${index}`}><span className="timeline-dot"/><div><strong>{displayState(row.status || row.event_type || 'Record')}</strong><small>{formatDate(row.timestamp || row.created_at)}</small></div></div>)}</div> : <p className="muted">No additional mirrored lifecycle history was returned.</p>}</div></div>
        <div className="ledger-events"><h3>Mirrored ledger events</h3>{events.length ? events.map((event,index) => <div className="ledger-event" key={event.id || event.tx_id || index}><div><strong>{displayState(event.event_type || event.type || event.status || 'Event')}</strong><small>{formatDate(event.timestamp || event.round_time || event.created_at)}</small></div>{(event.tx_hash || event.tx_id || event.transaction_id) && <a href={`https://lora.algokit.io/testnet/transaction/${encodeURIComponent(event.tx_hash || event.tx_id || event.transaction_id)}`} target="_blank" rel="noreferrer">View transaction <ArrowRight size={14}/></a>}</div>) : <p className="muted">No mirrored ledger events returned.</p>}</div>
      </Panel>
    </div>}
  </section>;
}

export function NetworkPage() {
  return <section className="page-wrap"><PageIntro eyebrow="TraceLoop network" title="A physical-first lifecycle network" description="TraceLoop coordinates device owners, buyers, authorized service centres, and recycling facilities through explicit handoffs and auditable lifecycle records."/><div className="value-grid network-grid"><article className="value-card"><span className="value-icon"><Users size={21}/></span><h3>Owners and buyers</h3><p>Register and transfer confirmed V2 passports. Participants exchange account IDs in person; private user information is not listed publicly.</p></article><article className="value-card"><span className="value-icon"><ShieldCheck size={21}/></span><h3>Authorized service centres</h3><p>Owners share a specific passport with a verifier. Physical inspection and backend authorization precede a verification stamp.</p></article><article className="value-card"><span className="value-icon"><Recycle size={21}/></span><h3>Recycling facilities</h3><p>Accept an ownership handoff, acknowledge physical receipt, and record the recycling state only after the required checks.</p></article></div><Panel title="How participants connect" description="TraceLoop does not expose a public directory of user accounts. Identifiers are exchanged directly at a physical handoff or service counter."><div className="network-steps"><div><span>01</span><strong>Meet in person</strong><p>Exchange the relevant TraceLoop user ID or passport ID.</p></div><div><span>02</span><strong>Share a record</strong><p>The owner creates an explicit verification request or transfer.</p></div><div><span>03</span><strong>Perform the real-world check</strong><p>Authorized participants inspect the device and use the corresponding workflow.</p></div><div><span>04</span><strong>Audit the lifecycle</strong><p>Track the public passport and available source reconciliation.</p></div></div><div className="panel-actions bottom-actions"><Link className="button button-primary" to="/track">Track a passport <ArrowRight size={16}/></Link><Link className="button button-secondary" to="/register">Join TraceLoop</Link></div></Panel></section>;
}

export function AdminPage() {
  const { toast } = useToast();
  const [approvals, setApprovals] = useState([]); const [documents, setDocuments] = useState([]); const [disputes, setDisputes] = useState([]); const [devices, setDevices] = useState([]); const [verifiers, setVerifiers] = useState([]);
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [showLegacy, setShowLegacy] = useState(false);
  const [viewed, setViewed] = useState({}); const [modal, setModal] = useState(null); const [busy, setBusy] = useState(false); const [notes, setNotes] = useState(''); const [reason, setReason] = useState(''); const [targetState, setTargetState] = useState('REGISTERED');
  const load = useCallback(async () => {
    setLoading(true); setError('');
    const outcomes = await Promise.allSettled([
      apiData('/admin/approvals/pending'), apiData('/admin/kyc/pending'), apiData('/disputes/open'), apiData('/admin/devices'), apiData('/admin/verifiers'),
    ]);
    const labels = ['account approvals', 'evidence queue', 'disputes', 'device registry', 'authorized service centres'];
    const failed = outcomes.map((result, idx) => result.status === 'rejected' ? `${labels[idx]}: ${result.reason.message}` : null).filter(Boolean);
    if (outcomes[0].status === 'fulfilled') setApprovals(outcomes[0].value.pending_accounts || []);
    if (outcomes[1].status === 'fulfilled') setDocuments(outcomes[1].value.pending_documents || []);
    if (outcomes[2].status === 'fulfilled') setDisputes(outcomes[2].value.disputes || []);
    if (outcomes[3].status === 'fulfilled') setDevices(outcomes[3].value.devices || []);
    if (outcomes[4].status === 'fulfilled') setVerifiers(outcomes[4].value.verifiers || []);
    setError(failed.join(' · ')); setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);
  const approveAccount = async (item) => { setBusy(true); try { const data = await apiData(`/admin/approvals/${encodeURIComponent(item.user_id)}/approve`, { method: 'POST', body: JSON.stringify({ notes: notes.trim() || null }) }); toast(data.message || 'Account approved.'); setModal(null); setNotes(''); await load(); } catch (e) { toast(e.message, 'error'); } finally { setBusy(false); } };
  const rejectAccount = async (event) => { event.preventDefault(); if (!modal?.item) return; setBusy(true); try { const data = await apiData(`/admin/approvals/${encodeURIComponent(modal.item.user_id)}/reject`, { method: 'POST', body: JSON.stringify({ notes: reason.trim() }) }); toast(data.message || 'Account rejection recorded.'); setModal(null); setReason(''); await load(); } catch (e) { toast(e.message, 'error'); } finally { setBusy(false); } };
  const openEvidence = async (item) => { const id = item.evidence_id; if (!id) { toast('This legacy record has no protected document file. It cannot be accepted as inspected evidence.', 'error'); return; } const popup = window.open('about:blank', '_blank'); try { const blob = await apiBlob(`/admin/evidence/${encodeURIComponent(id)}/view`); const url = URL.createObjectURL(blob); if (popup) { popup.location.href = url; } else { const link = document.createElement('a'); link.href = url; link.target = '_blank'; link.rel = 'noopener'; link.click(); } setViewed((old) => ({ ...old, [id]: true })); toast('Protected evidence opened. Review the actual document before deciding.'); window.setTimeout(() => URL.revokeObjectURL(url), 120000); } catch (e) { popup?.close(); toast(e.message, 'error'); } };
  const reviewEvidence = async (decision) => { if (!modal?.item) return; const item = modal.item; if (decision === 'ACCEPT' && !viewed[item.evidence_id]) { toast('Open the protected document viewer before accepting this evidence.', 'error'); return; } setBusy(true); try { const data = await apiData(`/admin/evidence/${encodeURIComponent(item.evidence_id)}/review`, { method: 'POST', body: JSON.stringify({ decision, rejection_reason: decision === 'REJECT' ? reason.trim() : null, review_notes: notes.trim() || null, document_inspected: decision === 'ACCEPT' }) }); toast(data.message || `Evidence ${decision.toLowerCase()}ed.`); setModal(null); setReason(''); setNotes(''); await load(); } catch (e) { toast(e.message, 'error'); } finally { setBusy(false); } };
  const freezeDispute = async (item) => { setBusy(true); try { const data = await apiData(`/disputes/${encodeURIComponent(item.dispute_id)}/freeze`, { method: 'POST' }); toast(data.message || 'Dispute freeze submitted on-chain.'); setModal(null); await load(); } catch (e) { toast(e.message, 'error'); } finally { setBusy(false); } };
  const resolveDispute = async (event) => { event.preventDefault(); if (!modal?.item) return; setBusy(true); try { const data = await apiData(`/disputes/${encodeURIComponent(modal.item.dispute_id)}/resolve`, { method: 'PATCH', body: JSON.stringify({ resolved_state: targetState, resolution_note: notes.trim() }) }); toast(data.message || 'Dispute resolution submitted on-chain.'); setModal(null); setNotes(''); await load(); } catch (e) { toast(e.message, 'error'); } finally { setBusy(false); } };
  const exportDevice = async (event) => { event.preventDefault(); if (!modal?.item) return; setBusy(true); try { const data = await apiData(`/admin/devices/${encodeURIComponent(modal.item.public_id || modal.item.device_id)}/export`, { method: 'POST', body: JSON.stringify({ notes: notes.trim() || 'Exported by administrator.' }) }); toast(data.message || 'Device export recorded on-chain.'); setModal(null); setNotes(''); await load(); } catch (e) { toast(e.message, 'error'); } finally { setBusy(false); } };
  const revokeVerifier = async () => { if (!modal?.item) return; setBusy(true); try { const data = await apiData(`/admin/verifiers/${encodeURIComponent(modal.item.user_id)}/revoke`, { method: 'POST' }); toast(data.message || 'Service-centre authorization revoked.'); setModal(null); await load(); } catch (e) { toast(e.message, 'error'); } finally { setBusy(false); } };
  const reviewableDisputes = disputes.filter((d) => !d.is_legacy_test && !d.read_only);
  const visibleDevices = devices.filter((d) => showLegacy || (!d.is_legacy_test && d.identity_version !== 'V1_LEGACY'));
  return <section className="page-wrap"><PageIntro eyebrow="TraceLoop administration" title="Operations console" description="Review account activation and evidence, supervise on-chain dispute actions, and inspect the registry." actions={<Button variant="secondary" icon={RefreshCw} onClick={load}>Refresh all queues</Button>}/>{error && <Notice kind="warning">Some queues could not be refreshed: {error}</Notice>}
    <div className="stat-grid modern-stats"><StatCard icon={Users} label="Account approvals" value={approvals.length} detail="Service centres and recyclers"/><StatCard icon={FileCheck2} label="Evidence awaiting review" value={documents.length} detail="Open actual files before accepting" tone="blue"/><StatCard icon={ShieldCheck} label="Active verifiers" value={verifiers.length} detail="Authorized service centres" tone="green"/><StatCard icon={Laptop} label="Confirmed V2 devices" value={devices.filter((d) => !d.is_legacy_test && d.registration_status === 'CONFIRMED').length} detail="V1 legacy rows hidden by default" tone="amber"/></div>
    {loading && <div className="skeleton-stack"><div className="skeleton-row"/><div className="skeleton-row"/><div className="skeleton-card"/></div>}
    <div className="admin-sections">
      <Panel title="Account activation" description="Only verified service-centre/recycler credentials can pass the separate activation step.">{approvals.length ? <div className="list-stack">{approvals.map((item) => <div className="work-row" key={item.user_id}><span className="work-row-icon"><Users size={18}/></span><div className="work-row-main"><strong>{item.name}</strong><small>{displayState(item.role)} · {item.phone}</small><small>Credential: {displayState(item.credential_status)} · {displayState(item.activation_stage)}</small><small className="mono">{item.user_id}</small></div><StateBadge value={item.user_status}/><div className="row-actions"><Button size="sm" onClick={() => { setNotes(''); setModal({ type: 'approve-account', item }); }} disabled={!item.can_activate}>Approve</Button><Button size="sm" variant="secondary" onClick={() => { setReason(''); setModal({ type: 'reject-account', item }); }}>Reject</Button></div></div>)}</div> : !loading && <EmptyState icon={CheckCircle2} title="No pending account approvals" description="Service-centre and recycler activation requests will appear here after their credential has been verified."/>}</Panel>
      <Panel title="Evidence review" description="Open each protected file before approving. The backend records the view action and requires an explicit inspection attestation.">{documents.length ? <div className="list-stack">{documents.map((item) => <div className="work-row work-row-tall" key={item.evidence_id || item.doc_id}><span className="work-row-icon"><FileText size={18}/></span><div className="work-row-main"><strong>{item.user_name || 'Account evidence'}</strong><small>{displayState(item.doc_type)} · {displayState(item.user_role)}</small><small>{item.serial_for_device ? `Serial claim: ${item.serial_for_device}` : item.claimed_document_number || item.original_filename || 'Document metadata unavailable'}</small><small>{formatDate(item.uploaded_at)} · {displayState(item.status)}</small></div><StateBadge value={item.status}/><div className="row-actions"><Button size="sm" variant="secondary" onClick={() => openEvidence(item)} disabled={!item.evidence_id || !item.has_stored_file}>{viewed[item.evidence_id] ? 'Opened' : 'Review file'}</Button><Button size="sm" onClick={() => { setReason(''); setNotes(''); setModal({ type: 'review-evidence', item }); }} disabled={!item.evidence_id || !item.can_approve}>Decide</Button></div></div>)}</div> : !loading && <EmptyState icon={FileCheck2} title="Evidence queue is clear" description="New uploaded documents will appear here for protected review."/>}</Panel>
      <Panel title="Dispute review" description="UNDER REVIEW means reported but not frozen. Freezing writes to Algorand. OPEN disputes can be resolved to an allowed state, after which the device needs re-verification.">{reviewableDisputes.length ? <div className="list-stack">{reviewableDisputes.map((item) => <div className="work-row work-row-tall" key={item.dispute_id}><span className="work-row-icon"><ShieldCheck size={18}/></span><div className="work-row-main"><strong>{item.brand_name || 'Device issue'} · {displayState(item.dispute_type)}</strong><small className="mono">{item.device_id}</small><p>{item.description}</p><small>Reported by {displayState(item.raised_by_role)} · {formatDate(item.created_at)}</small></div><StateBadge value={item.status}/><div className="row-actions">{item.status === 'UNDER_REVIEW' && <Button size="sm" variant="danger" onClick={() => setModal({ type: 'freeze-dispute', item })}>Freeze on-chain</Button>}{item.status === 'OPEN' && <Button size="sm" onClick={() => { setNotes(''); setTargetState('REGISTERED'); setModal({ type: 'resolve-dispute', item }); }}>Resolve</Button>}</div></div>)}</div> : !loading && <EmptyState icon={ShieldCheck} title="No active V2 disputes" description="Disputes awaiting review or resolution will appear here."/>}</Panel>
      <Panel title="Device registry" description="V1 historical test records are hidden by default. Export is an irreversible on-chain action."><div className="legacy-toggle"><label className="check-label"><input type="checkbox" checked={showLegacy} onChange={(e) => setShowLegacy(e.target.checked)}/> Show V1 legacy test records</label></div>{visibleDevices.length ? <div className="list-stack">{visibleDevices.map((item) => <div className="work-row" key={item.public_id || item.device_id}><span className="work-row-icon"><Laptop size={18}/></span><div className="work-row-main"><strong>{item.brand || 'Device'}</strong><small className="mono">{item.public_id || item.device_id}</small><small>{displayState(item.identity_version)} · {displayState(item.registration_status)}</small></div><StateBadge value={item.status}/>{!item.read_only && !['RECYCLED','EXPORTED'].includes(String(item.status).toUpperCase()) && <Button size="sm" variant="danger" onClick={() => { setNotes(''); setModal({ type: 'export-device', item }); }}>Export</Button>}</div>)}</div> : !loading && <EmptyState icon={Laptop} title="No devices to show" description="Device records will appear here as registrations are created."/>}</Panel>
      <Panel title="Authorized service centres" description="Admin-only view. These details are not exposed as a public directory or returned to device owners.">{verifiers.length ? <div className="list-stack">{verifiers.map((v) => <div className="work-row" key={v.user_id}><span className="work-row-icon"><ShieldCheck size={18}/></span><div className="work-row-main"><strong>{v.name}</strong><small>{v.phone} · Centre {v.service_center_id || '—'}</small><small className="mono">{v.user_id}</small></div><StateBadge value={v.status}/><Button size="sm" variant="danger" onClick={() => setModal({ type: 'revoke-verifier', item: v })}>Revoke</Button></div>)}</div> : !loading && <EmptyState icon={ShieldCheck} title="No currently eligible verifiers" description="Only active, eligible service-centre accounts are listed here."/>}</Panel>
    </div>
    <Modal open={modal?.type === 'approve-account'} title="Approve account" description={`Approve and activate ${modal?.item?.name || 'this account'}?`} onClose={() => !busy && setModal(null)}><p className="muted">The backend has confirmed eligible role evidence. Approval activates this service-centre or recycler account.</p><TextArea label="Admin note (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} maxLength={3000}/><div className="modal-actions"><Button variant="secondary" onClick={() => setModal(null)} disabled={busy}>Cancel</Button><Button onClick={() => approveAccount(modal.item)} loading={busy}>Approve account</Button></div></Modal>
    <Modal open={modal?.type === 'reject-account'} title="Reject account activation" description={`Reject ${modal?.item?.name || 'this account'}? Provide a reason for the audit trail.`} onClose={() => !busy && setModal(null)}><form className="form-stack" onSubmit={rejectAccount}><TextArea label="Reason / admin notes" value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} maxLength={3000} rows={3}/><div className="modal-actions"><Button variant="secondary" type="button" onClick={() => setModal(null)} disabled={busy}>Cancel</Button><Button variant="danger" type="submit" loading={busy}>Reject account</Button></div></form></Modal>
    <Modal open={modal?.type === 'review-evidence'} title="Review evidence" description="Open the protected file and verify the actual document before accepting it." onClose={() => !busy && setModal(null)}>{modal?.item && <div className="evidence-review-summary"><strong>{modal.item.user_name || 'Account evidence'}</strong><p>{displayState(modal.item.doc_type)} · {modal.item.serial_for_device || modal.item.claimed_document_number || 'No serial claim'}</p><p>{modal.item.original_filename || 'Uploaded evidence'} · {displayState(modal.item.status)}</p></div>}{!viewed[modal?.item?.evidence_id] && <Notice kind="warning">Open the protected document before the backend will allow acceptance.</Notice>}{viewed[modal?.item?.evidence_id] && <Notice kind="success">Protected document has been opened in this session.</Notice>}<Button variant="secondary" onClick={() => modal?.item && openEvidence(modal.item)} disabled={!modal?.item?.has_stored_file}>Open protected file <ArrowRight size={15}/></Button><TextArea label="Review notes (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} maxLength={3000}/><TextArea label="Rejection reason (required only for reject)" value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={3000}/><div className="modal-actions"><Button variant="secondary" onClick={() => setModal(null)} disabled={busy}>Close</Button><Button variant="danger" onClick={() => reviewEvidence('REJECT')} loading={busy} disabled={!reason.trim()}>Reject</Button><Button onClick={() => reviewEvidence('ACCEPT')} loading={busy} disabled={!viewed[modal?.item?.evidence_id] || !modal?.item?.can_approve}>Accept evidence</Button></div></Modal>
    <Modal open={modal?.type === 'freeze-dispute'} title="Freeze device on-chain" description="This writes an irreversible dispute/freeze action to Algorand TestNet. Confirm the report and V2 device before continuing." onClose={() => !busy && setModal(null)}><div className="current-spec"><small>Device</small><strong>{modal?.item?.brand_name} · {modal?.item?.device_id}</strong><small>{modal?.item?.description}</small></div><div className="modal-actions"><Button variant="secondary" onClick={() => setModal(null)} disabled={busy}>Cancel</Button><Button variant="danger" onClick={() => freezeDispute(modal.item)} loading={busy}>Freeze on-chain</Button></div></Modal>
    <Modal open={modal?.type === 'resolve-dispute'} title="Resolve dispute" description="Choose the allowed state that matches the reviewed outcome. Ownership is not reverted by this operation; the device will require re-verification." onClose={() => !busy && setModal(null)}><form className="form-stack" onSubmit={resolveDispute}><SelectInput label="Resolved lifecycle state" value={targetState} onChange={(e) => setTargetState(e.target.value)}><option value="REGISTERED">Registered</option><option value="VERIFIED">Verified</option><option value="TRANSFERRED">Transferred</option></SelectInput><TextArea label="Resolution note" value={notes} onChange={(e) => setNotes(e.target.value)} minLength={10} maxLength={3000} required rows={4} placeholder="Record the basis for the resolution (at least 10 characters)."/><div className="modal-actions"><Button variant="secondary" type="button" onClick={() => setModal(null)} disabled={busy}>Cancel</Button><Button type="submit" loading={busy}>Resolve on-chain</Button></div></form></Modal>
    <Modal open={modal?.type === 'export-device'} title="Export device on-chain" description="This marks a confirmed V2 device as EXPORTED on Algorand and is not reversible through the UI." onClose={() => !busy && setModal(null)}><form className="form-stack" onSubmit={exportDevice}><TextArea label="Admin note" value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} maxLength={3000} placeholder="Reason for the export"/><div className="modal-actions"><Button variant="secondary" type="button" onClick={() => setModal(null)} disabled={busy}>Cancel</Button><Button type="submit" variant="danger" loading={busy}>Export on-chain</Button></div></form></Modal>
    <ConfirmModal open={modal?.type === 'revoke-verifier'} title="Revoke service-centre access?" description={`This suspends ${modal?.item?.name || 'the verifier'} and removes active service-centre privileges. Existing verification requests may become unable to proceed.`} actionLabel="Revoke access" danger onCancel={() => setModal(null)} onConfirm={revokeVerifier} busy={busy}/>
  </section>;
}


export function AccountPage() {
  const { profile, signOut } = useAuth();
  const { toast } = useToast();
  const navigate = useNavigate();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');
  const [rewardData, setRewardData] = useState(null);
  const [rewardRows, setRewardRows] = useState([]);
  const [rewardLoading, setRewardLoading] = useState(true);
  const [rewardMoreLoading, setRewardMoreLoading] = useState(false);
  const [rewardError, setRewardError] = useState('');

  const loadRewards = useCallback(async () => {
    setRewardLoading(true);
    setRewardError('');
    try {
      const data = await apiData('/api/v1/rewards/me?limit=20&offset=0');
      setRewardData(data);
      setRewardRows(Array.isArray(data.items) ? data.items : []);
    } catch (e) {
      setRewardError(e.message || 'Eco Points history could not be loaded.');
    } finally {
      setRewardLoading(false);
    }
  }, []);

  useEffect(() => { loadRewards(); }, [loadRewards]);

  const loadMoreRewards = async () => {
    if (rewardMoreLoading || rewardRows.length >= Number(rewardData?.transactions_count || 0)) return;
    setRewardMoreLoading(true);
    setRewardError('');
    try {
      const data = await apiData(`/api/v1/rewards/me?limit=20&offset=${rewardRows.length}`);
      setRewardData(data);
      setRewardRows((previous) => [...previous, ...(Array.isArray(data.items) ? data.items : [])]);
    } catch (e) {
      setRewardError(e.message || 'More reward transactions could not be loaded.');
    } finally {
      setRewardMoreLoading(false);
    }
  };

  const deleteAccount = async () => {
    setDeleting(true); setError('');
    try {
      const data = await apiData('/api/v1/me', { method: 'DELETE' });
      signOut();
      setConfirmDelete(false);
      toast(data.message || 'Account deletion request completed.');
      navigate('/', { replace: true });
    } catch (e) { setError(e.message); }
    finally { setDeleting(false); }
  };

  const pointsBalance = Number(rewardData?.balance_points ?? profile?.reward_points ?? 0);
  const transactionsPointTotal = Number(rewardData?.transaction_points_total ?? 0);
  const historyMatchesBalance = rewardData?.balance_matches_history;
  const actionLabel = (item) => item.action_label || displayState(item.action || 'REWARD');

  return <section className="page-wrap narrow">
    <PageIntro eyebrow="Account" title="Your TraceLoop account" description="Manage the identifier you exchange with other participants and review account access details."/>

    <Panel title="Eco Points" description="Reward points recorded for your participation in device lifecycle activities. Your balance and transaction history are shown separately so older V1 records remain visible." actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={loadRewards} loading={rewardLoading}>Refresh</Button>}>
      <div className="reward-overview">
        <div className="reward-balance-card">
          <span className="reward-balance-label"><Leaf size={17}/> Current balance</span>
          <strong>{rewardLoading && !rewardData ? '…' : pointsBalance.toLocaleString()}</strong>
          <small>Eco Points</small>
        </div>
        <div className="reward-history-summary">
          <div><span>Recorded transactions</span><strong>{rewardLoading && !rewardData ? '…' : Number(rewardData?.transactions_count || 0).toLocaleString()}</strong></div>
          <div><span>Points in transaction history</span><strong>{rewardLoading && !rewardData ? '…' : transactionsPointTotal.toLocaleString()}</strong></div>
        </div>
      </div>
      {historyMatchesBalance === false && <Notice kind="warning">Your saved Eco Points balance and the total of your recorded reward transactions do not match. The balance above comes from your account record; the history below shows the saved transactions. TraceLoop has not changed or re-awarded any points automatically.</Notice>}
      {rewardError && <Notice kind="error">{rewardError}<div><button type="button" className="text-button" onClick={loadRewards}>Retry loading rewards</button></div></Notice>}
      <div className="reward-history-heading"><div><h3>Reward history</h3><p>Newest activity appears first.</p></div>{rewardData?.transactions_count > 0 && <Badge>{rewardRows.length} of {rewardData.transactions_count}</Badge>}</div>
      {rewardLoading && !rewardRows.length ? <div className="skeleton-stack"><div className="skeleton-row"/><div className="skeleton-row"/></div>
        : rewardRows.length ? <div className="reward-history-list">{rewardRows.map((item) => {
          const pointValue = Number(item.points || 0);
          return <article className="reward-history-row" key={item.reward_id}>
            <span className="reward-history-icon"><Leaf size={17}/></span>
            <div className="reward-history-copy">
              <strong>{actionLabel(item)}</strong>
              <small>{formatDate(item.created_at)}</small>
              {item.public_device_id && <Link className="reward-passport-link" to={`/track?id=${encodeURIComponent(item.public_device_id)}`}>Passport {item.public_device_id} <ArrowRight size={13}/></Link>}
            </div>
            <strong className={`reward-points-value ${pointValue < 0 ? 'reward-points-negative' : 'reward-points-positive'}`}>{pointValue > 0 ? '+' : ''}{pointValue.toLocaleString()} pts</strong>
          </article>;
        })}</div>
        : !rewardLoading && <EmptyState icon={Leaf} title="No reward transactions yet" description="Eco Points activity will appear here when a reward transaction is recorded for your account."/>}
      {rewardData?.has_more && <div className="reward-load-more"><Button variant="secondary" onClick={loadMoreRewards} loading={rewardMoreLoading}>Load older transactions <ArrowRight size={15}/></Button></div>}
    </Panel>

    <Panel title="Account details" description="Copy this ID when a buyer, owner, recycler, or service centre needs to address your account in a physical workflow."><div className="account-detail-list"><div><small>Name</small><strong>{profile?.name || 'TraceLoop member'}</strong></div><div><small>Phone</small><strong>{profile?.phone || '—'}</strong></div><div><small>Role</small><strong>{displayState(profile?.role || 'UNKNOWN')}</strong></div><div><small>Account status</small><StateBadge value={profile?.status}/></div></div><div className="identifier-card"><div><small>Your TraceLoop user ID</small><strong className="mono">{profile?.user_id}</strong></div><CopyButton value={profile?.user_id} onCopy={toast}/></div><Notice kind="info">This is your account user ID. A device Passport ID is separate. Share only the identifier needed for the action you are completing.</Notice></Panel>
    <Panel title="Account deletion" description="Use this only if you intend to remove your TraceLoop account. Linked records may prevent the current endpoint from deleting the account row."><Notice kind="warning">The current backend endpoint does not rewrite or delete immutable on-chain history and is not a complete data-erasure/anonymization workflow. Linked device, transfer, evidence, or audit records may cause deletion to fail.</Notice>{error && <Notice kind="error">{error}</Notice>}<Button variant="danger" onClick={() => setConfirmDelete(true)}><Trash2 size={16}/> Delete account</Button></Panel>
    <ConfirmModal open={confirmDelete} title="Delete this TraceLoop account?" description="This sends the existing account-deletion request. On-chain history is not changed, and linked database records may prevent deletion. This cannot be treated as a complete erasure of historical data." actionLabel="Submit deletion request" danger onCancel={() => setConfirmDelete(false)} onConfirm={deleteAccount} busy={deleting}/>
  </section>;
}
