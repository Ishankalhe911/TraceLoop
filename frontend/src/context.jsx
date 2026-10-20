import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { apiData, clearSession, getCachedUser, getToken, saveSession } from './api.js';
import { ToastViewport } from './ui.jsx';

const AuthContext = createContext(null);
const ToastContext = createContext(null);
export const useAuth = () => useContext(AuthContext);
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const dismiss = useCallback((id) => setItems((old) => old.filter((item) => item.id !== id)), []);
  const toast = useCallback((message, kind = 'success') => {
    const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setItems((old) => [...old, { id, message, kind }].slice(-4));
    window.setTimeout(() => dismiss(id), 4300);
  }, [dismiss]);
  const value = useMemo(() => ({ toast }), [toast]);
  return <ToastContext.Provider value={value}>{children}<ToastViewport items={items} dismiss={dismiss} /></ToastContext.Provider>;
}

export function AuthProvider({ children }) {
  const [profile, setProfile] = useState(getCachedUser());
  const [loading, setLoading] = useState(Boolean(getToken()));

  const refreshProfile = useCallback(async () => {
    if (!getToken()) {
      setProfile(null);
      setLoading(false);
      return null;
    }
    try {
      const data = await apiData('/api/v1/me');
      setProfile(data);
      localStorage.setItem('traceloop_user', JSON.stringify(data));
      return data;
    } catch (error) {
      if (error.status === 401) {
        clearSession();
        setProfile(null);
      } else {
        throw error;
      }
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refreshProfile().catch(() => setLoading(false)); }, [refreshProfile]);

  const signIn = useCallback((payload) => {
    saveSession(payload);
    setProfile({
      ...(getCachedUser() || {}),
      user_id: payload.user_id,
      role: payload.role,
      status: payload.status,
    });
  }, []);

  const signOut = useCallback(() => {
    clearSession();
    setProfile(null);
  }, []);

  const value = useMemo(() => ({ profile, loading, refreshProfile, signIn, signOut, isSignedIn: Boolean(getToken()) }), [profile, loading, refreshProfile, signIn, signOut]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
