'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

type Role = 'super_admin' | 'grid_operator' | 'research_analyst';

type User = {
  id: string;
  email: string;
  display_name: string;
  role: Role;
};

type LoginCredentials = {
  email: string;
  password: string;
};

type AuthContextValue = {
  user: User | null;
  accessToken: string | null;
  loading: boolean;
  login: (credentials: LoginCredentials) => Promise<string>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<string | null>;
};

type AuthResponse = {
  access_token: string;
  user: User;
};

const TOKEN_RENEW_MS = 12 * 60 * 1000;

const AuthContext = createContext<AuthContextValue | null>(null);

async function parseResponse(response: Response): Promise<AuthResponse> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || 'Unable to authenticate');
  }
  return response.json();
}

export function AuthProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  const [user, setUser] = useState<User | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // The backend rotates the refresh token and revokes the previous jti on every
  // call, so two concurrent refreshes would send the same cookie and the second
  // one would 401 against the just-revoked token. Share a single in-flight
  // promise so overlapping callers (StrictMode's double effect, parallel API
  // retries) resolve from one request.
  const inFlightRefresh = useRef<Promise<string | null> | null>(null);

  const requestRefresh = async () => {
    try {
      const response = await fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' });
      if (!response.ok) {
        setUser(null);
        setAccessToken(null);
        return null;
      }
      const payload = await response.json() as AuthResponse;
      setAccessToken(payload.access_token);
      setUser(payload.user);
      return payload.access_token;
    } catch {
      setUser(null);
      setAccessToken(null);
      return null;
    }
  };

  // Stable identity so hooks that depend on it (useApi) don't re-run effects every render.
  // requestRefresh only touches state setters and refs, so the first closure stays valid.
  const refreshSession = useCallback(() => {
    if (!inFlightRefresh.current) {
      inFlightRefresh.current = requestRefresh().finally(() => {
        inFlightRefresh.current = null;
      });
    }
    return inFlightRefresh.current;
  }, []);

  useEffect(() => {
    refreshSession().finally(() => setLoading(false));
  }, []);

  // Access tokens last 15 minutes (ACCESS_TOKEN_MINUTES). Renew ahead of expiry so pages
  // left open keep working instead of failing with "Invalid or expired token".
  useEffect(() => {
    if (!accessToken) return;
    const timer = window.setTimeout(() => { void refreshSession(); }, TOKEN_RENEW_MS);
    return () => window.clearTimeout(timer);
  }, [accessToken]);

  const login = async (credentials: LoginCredentials) => {
    const response = await fetch('/api/auth/login', {
      body: JSON.stringify(credentials),
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      method: 'POST'
    });
    const payload = await parseResponse(response);
    setAccessToken(payload.access_token);
    setUser(payload.user);
    return payload.access_token;
  };

  const logout = async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
    } finally {
      setUser(null);
      setAccessToken(null);
    }
  };

  const value = useMemo(() => ({ user, accessToken, loading, login, logout, refreshSession }), [user, accessToken, loading, refreshSession]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}

export type { Role, User };
