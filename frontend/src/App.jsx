import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, Navigate, NavLink, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { Activity, ArrowRight, Bell, CheckCheck, Copy, Laptop, Leaf, LogOut, Menu, ShieldCheck, X } from 'lucide-react';
import { useAuth, useToast, AuthProvider, ToastProvider } from './context.jsx';
import { CopyButton, IconButton } from './ui.jsx';
import { apiData, formatDate } from './api.js';
import {
  AdminPage, DashboardPage, LandingPage, LoginPage, NetworkPage,
  AccountPage, OnboardingPage, RecyclingPage, RegisterDevicePage, RegisterPage,
  TrackPage, TransferPage,
} from './pages.jsx';

function NotificationCenter() {
  const { profile } = useAuth();
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  const loadNotifications = useCallback(async ({ quiet = false } = {}) => {
    if (!profile?.user_id || !['ACTIVE', 'KYC_IN_PROGRESS', 'REJECTED'].includes(profile.status)) return;
    if (!quiet) setLoading(true);
    try {
      const data = await apiData('/api/v1/notifications?limit=40');
      setItems(Array.isArray(data.items) ? data.items : []);
      setUnreadCount(Number(data.unread_count) || 0);
      setError('');
    } catch (err) {
      if (!quiet) setError(err.message || 'Notifications could not be loaded.');
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [profile?.user_id, profile?.status]);

  useEffect(() => {
    loadNotifications();
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') loadNotifications({ quiet: true });
    }, 30000);
    const onFocus = () => loadNotifications({ quiet: true });
    const onVisibility = () => { if (document.visibilityState === 'visible') loadNotifications({ quiet: true }); };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [loadNotifications]);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutside = (event) => { if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false); };
    const closeOnEscape = (event) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', closeOnOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [open]);

  if (!profile || !['ACTIVE', 'KYC_IN_PROGRESS', 'REJECTED'].includes(profile.status)) return null;

  const markRead = async (item) => {
    if (item.read_at) return;
    try {
      await apiData(`/api/v1/notifications/${encodeURIComponent(item.id)}/read`, { method: 'POST' });
      setItems((current) => current.map((row) => row.id === item.id ? { ...row, read_at: new Date().toISOString() } : row));
      setUnreadCount((count) => Math.max(0, count - 1));
    } catch (err) {
      setError(err.message || 'Could not update notification state.');
    }
  };

  const openNotification = async (item) => {
    await markRead(item);
    setOpen(false);
    const destination = typeof item.action_path === 'string' && item.action_path.startsWith('/') && !item.action_path.startsWith('//')
      ? item.action_path : '/dashboard';
    navigate(destination);
  };

  const markAllRead = async () => {
    try {
      await apiData('/api/v1/notifications/read-all', { method: 'POST' });
      const now = new Date().toISOString();
      setItems((current) => current.map((row) => ({ ...row, read_at: row.read_at || now })));
      setUnreadCount(0);
      setError('');
    } catch (err) { setError(err.message || 'Could not mark notifications as read.'); }
  };

  return <div className="notification-wrap" ref={rootRef}>
    <button type="button" className={`notification-trigger ${open ? 'is-open' : ''}`} aria-label={`Notifications${unreadCount ? `, ${unreadCount} unread` : ''}`} aria-expanded={open} aria-haspopup="dialog" onClick={() => { setOpen((value) => !value); if (!open) loadNotifications(); }}>
      <Bell size={18} /><span className="notification-label">Notifications</span>{unreadCount > 0 && <span className="notification-count">{unreadCount > 99 ? '99+' : unreadCount}</span>}
    </button>
    {open && <section className="notification-popover" role="dialog" aria-label="Notification centre">
      <div className="notification-head"><div><h2>Notifications</h2><p>{unreadCount ? `${unreadCount} unread update${unreadCount === 1 ? '' : 's'}` : 'You’re up to date'}</p></div><div className="notification-head-actions"><button type="button" className="notification-icon-action" aria-label="Refresh notifications" title="Refresh" onClick={() => loadNotifications()}><Activity size={16}/></button><button type="button" className="notification-icon-action" aria-label="Close notifications" title="Close" onClick={() => setOpen(false)}><X size={16}/></button></div></div>
      {error && <div className="notification-error" role="alert">{error}<button type="button" onClick={() => loadNotifications()}>Retry</button></div>}
      {loading && !items.length ? <div className="notification-empty"><span className="spinner"/><p>Checking for updates…</p></div> : items.length ? <div className="notification-list">{items.map((item) => <button key={item.id} type="button" className={`notification-item ${item.read_at ? '' : 'is-unread'}`} onClick={() => openNotification(item)}><span className="notification-item-dot" aria-hidden="true"/><span className="notification-item-copy"><strong>{item.title}</strong><span>{item.message}</span><small>{formatDate(item.created_at)}</small></span><ArrowRight size={15} className="notification-item-arrow"/></button>)}</div> : !error ? <div className="notification-empty"><span className="notification-empty-icon"><Bell size={20}/></span><strong>No updates yet</strong><p>Important changes to your devices and handoffs will appear here, even if you were away.</p></div> : null}
      <div className="notification-foot">{unreadCount > 0 ? <button type="button" onClick={markAllRead}><CheckCheck size={15}/> Mark all as read</button> : <span>New lifecycle updates appear automatically.</span>}</div>
    </section>}
  </div>;
}

function Header() {
  const { profile, signOut } = useAuth();
  const { toast } = useToast();
  const [mobileOpen, setMobileOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => setMobileOpen(false), [location.pathname]);

  const links = profile ? [
    { to: profile.role === 'RECYCLER' ? '/recycling' : '/dashboard', label: profile.role === 'RECYCLER' ? 'Recycling desk' : 'Workspace' },
    ...(profile.role === 'FIRST_BUYER' ? [{ to: '/devices/new', label: 'Register device' }] : []),
    ...(['FIRST_BUYER', 'BUYER', 'RESELLER'].includes(profile.role) ? [{ to: '/transfer', label: 'Transfers' }] : []),
    ...(profile.role === 'ADMIN' ? [{ to: '/admin', label: 'Admin console' }] : []),
    { to: '/account', label: 'Account' },
    ...(profile.status !== 'SUSPENDED' ? [{ to: '/onboarding', label: 'KYC & evidence' }] : []),
    { to: '/track', label: 'Track passport' },
    { to: '/network', label: 'Network' },
  ] : [
    { to: '/track', label: 'Track passport' },
    { to: '/network', label: 'Network' },
  ];

  return <header className="app-header">
    <div className="header-inner">
      <Link to="/" className="brand" aria-label="TraceLoop home"><span className="brand-mark"><Activity size={20} /></span><span>Trace<span>Loop</span></span></Link>
      <button className="mobile-menu-button" onClick={() => setMobileOpen((value) => !value)} aria-label={mobileOpen ? 'Close navigation' : 'Open navigation'}>{mobileOpen ? <X /> : <Menu />}</button>
      <nav className={`main-nav ${mobileOpen ? 'is-open' : ''}`} aria-label="Main navigation">
        {links.map((item) => <NavLink key={item.to} to={item.to} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>{item.label}</NavLink>)}
        {profile && <NotificationCenter />}
        {profile ? <div className="nav-account">
          <span className="user-avatar">{String(profile.name || profile.role || 'T').slice(0, 1).toUpperCase()}</span>
          <div className="account-meta"><strong>{profile.name || 'TraceLoop member'}</strong><small>{profile.role ? profile.role.replaceAll('_', ' ') : 'Member'}</small></div>
          <CopyButton value={profile.user_id} onCopy={toast} />
          <IconButton label="Sign out" onClick={() => { signOut(); navigate('/'); toast('Signed out.'); }}><LogOut size={17} /></IconButton>
        </div> : <div className="nav-auth"><Link className="nav-login" to="/login">Sign in</Link><Link className="button button-primary button-sm" to="/register">Get started <ArrowRight size={15} /></Link></div>}
      </nav>
    </div>
  </header>;
}

function AppShell() {
  const { profile } = useAuth();
  return <div className="app-shell"><Header /><main className="main-content"><Outlet /></main><footer className="app-footer"><div className="footer-inner"><Link to="/" className="brand brand-small"><span className="brand-mark"><Leaf size={15} /></span><span>Trace<span>Loop</span></span></Link><p>Lifecycle records for more responsible device reuse.</p><div className="footer-links"><Link to="/track">Track a passport</Link><Link to="/network">Network</Link>{profile ? <Link to="/dashboard">Workspace</Link> : <Link to="/register">Get started</Link>}</div></div><small className="footer-bottom">TraceLoop · Device lifecycle passport</small></footer></div>;
}

function RequireAuth({ children }) {
  const { isSignedIn, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="page-loader"><span className="spinner" /><p>Restoring your session…</p></div>;
  if (!isSignedIn) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children || <Outlet />;
}

function RoleRoute({ roles, children }) {
  const { profile, loading } = useAuth();
  if (loading) return <div className="page-loader"><span className="spinner" /><p>Loading your workspace…</p></div>;
  if (!profile) return <Navigate to="/login" replace />;
  if (roles && !roles.includes(profile.role)) return <Navigate to="/dashboard" replace />;
  return children;
}

function AppRoutes() {
  return <Routes>
    <Route element={<AppShell />}>
      <Route index element={<LandingPage />} />
      <Route path="login" element={<LoginPage />} />
      <Route path="register" element={<RegisterPage />} />
      <Route path="onboarding" element={<RequireAuth><OnboardingPage /></RequireAuth>} />
      <Route path="account" element={<RequireAuth><AccountPage /></RequireAuth>} />
      <Route path="dashboard" element={<RequireAuth><DashboardPage /></RequireAuth>} />
      <Route path="devices/new" element={<RoleRoute roles={['FIRST_BUYER']}><RegisterDevicePage /></RoleRoute>} />
      <Route path="transfer" element={<RoleRoute roles={['FIRST_BUYER', 'BUYER', 'RESELLER']}><TransferPage /></RoleRoute>} />
      <Route path="recycling" element={<RoleRoute roles={['RECYCLER']}><RecyclingPage /></RoleRoute>} />
      <Route path="track" element={<TrackPage />} />
      <Route path="network" element={<NetworkPage />} />
      <Route path="admin" element={<RoleRoute roles={['ADMIN']}><AdminPage /></RoleRoute>} />
      <Route path="*" element={<NotFound />} />
    </Route>
  </Routes>;
}

function NotFound() {
  return <section className="page-wrap narrow"><div className="empty-state"><span className="empty-icon"><Laptop size={22} /></span><h2>Page not found</h2><p>This TraceLoop page doesn't exist or may have moved.</p><Link className="button button-primary" to="/">Return home</Link></div></section>;
}

export default function App() {
  return <ToastProvider><AuthProvider><AppRoutes /></AuthProvider></ToastProvider>;
}
