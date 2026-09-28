'use client';

import { useAuth } from './AuthProvider';

export function AdminOnly({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  return user?.role === 'super_admin' ? <>{children}</> : null;
}
