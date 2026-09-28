'use client';

import { useAuth } from './AuthProvider';
import { roleLabels } from './AppShell';

const ACCESS: Record<string, string> = {
  super_admin: 'All workspaces, users, settings and audit logs',
  grid_operator: 'Live grid, forecasts, peaks, alerts and recommendations',
  research_analyst: 'Live grid, datasets, model training, comparison and research',
};

export function ProfileDetails() {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <div className="metric-grid">
      <article className="metric-card metric-card-blue"><p>Name</p><strong>{user.display_name}</strong><span>{user.email}</span></article>
      <article className="metric-card metric-card-green"><p>Role</p><strong>{roleLabels[user.role]}</strong><span>{ACCESS[user.role]}</span></article>
      <article className="metric-card metric-card-amber"><p>Workspace</p><strong>Delhi</strong><span>Delhi SLDC grid</span></article>
      <article className="metric-card metric-card-slate"><p>Session</p><strong>Active</strong><span>Signed in with a secure refresh cookie</span></article>
    </div>
  );
}
