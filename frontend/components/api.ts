'use client';

import { useCallback } from 'react';
import { useAuth } from './AuthProvider';

export async function apiRequest<T>(path: string, token: string, init?: RequestInit, onUnauthorized?: () => Promise<string | null>): Promise<T> {
  const send = (bearer: string) => fetch(path, { ...init, headers: { Authorization: `Bearer ${bearer}`, ...(init?.headers || {}) } });
  let response = await send(token);
  if (response.status === 401 && onUnauthorized) {
    const renewed = await onUnauthorized();
    if (renewed) response = await send(renewed);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === 'string' ? payload.detail : 'The request could not be completed.');
  }
  return response.json() as Promise<T>;
}

/** Authenticated fetch that renews an expired session once and retries. */
export function useApi() {
  const { accessToken, refreshSession } = useAuth();
  return useCallback(<T,>(path: string, init?: RequestInit) => {
    if (!accessToken) return Promise.reject(new Error('Your session has ended. Sign in again to continue.'));
    return apiRequest<T>(path, accessToken, init, refreshSession);
  }, [accessToken, refreshSession]);
}
