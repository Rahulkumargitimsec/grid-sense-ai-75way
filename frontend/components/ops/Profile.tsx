'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useApi } from '../api';
import { roleLabels } from '../AppShell';
import { useAuth } from '../AuthProvider';
import { CATEGORY_LABELS } from './Alerts';
import { errorText, Loading, Section, timeAgo, when } from './ui';

type Preferences = { landing_page: string; theme: 'dark' | 'light'; alert_min_severity: 'low' | 'medium' | 'high' | 'critical'; alert_categories: string[] };
type Activity = { id: number; action: string; resource_type: string; resource_id: string | null; details: Record<string, unknown>; created_at: string };

const ACCESS: Record<string, string[]> = {
  super_admin: ['Every workspace', 'Users, audit log and settings', 'Model training and research', 'Alerts and recommendations'],
  grid_operator: ['Live grid, dashboard and forecasts', 'Alerts and recommendations', 'AI explanations and analytics', 'Reports'],
  research_analyst: ['Live grid, analytics and AI explanations', 'Datasets and model training', 'Model comparison and research', 'Reports'],
};
const PAGES: Record<string, string> = {
  '/live-grid': 'Live Grid', '/dashboard': 'Dashboard', '/peak-prediction': 'High & low demand forecast', '/analytics': 'Analytics', '/alerts': 'Alerts',
  '/recommendations': 'Recommendations', '/ai-explanation': 'AI Explanation', '/datasets': 'Datasets', '/research': 'Research',
};
const ANALYST_PAGES = ['/live-grid', '/analytics', '/ai-explanation', '/datasets', '/research'];

function actionText(item: Activity) {
  const text = item.action.replaceAll('_', ' ');
  const detail = item.resource_id ? ` · ${item.resource_type.replaceAll('_', ' ')} ${item.resource_id}` : ` · ${item.resource_type.replaceAll('_', ' ')}`;
  return text[0].toUpperCase() + text.slice(1) + detail;
}

export function applyTheme(theme: 'dark' | 'light') {
  document.documentElement.dataset.theme = theme;
  try { window.localStorage.setItem('gridsense-theme', theme); } catch { /* storage unavailable */ }
}

function PasswordForm() {
  const api = useApi();
  const [state, setState] = useState<{ error?: string; done?: boolean; busy?: boolean }>({});
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    if (data.get('new_password') !== data.get('confirm')) { setState({ error: 'The new passwords do not match.' }); return; }
    setState({ busy: true });
    try {
      await api('/api/v1/users/me/password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ current_password: data.get('current_password'), new_password: data.get('new_password') }) });
      form.reset();
      setState({ done: true });
    } catch (e) { setState({ error: errorText(e, 'Unable to change the password.') }); }
  };
  return (
    <form className="gw-card ops-form" onSubmit={(event) => void submit(event)}>
      <label>Current password<input name="current_password" type="password" autoComplete="current-password" required /></label>
      <div className="ops-form-grid ops-form-grid-2">
        <label>New password<input name="new_password" type="password" autoComplete="new-password" minLength={10} required /></label>
        <label>Confirm new password<input name="confirm" type="password" autoComplete="new-password" minLength={10} required /></label>
      </div>
      <p className="gw-footnote">At least 10 characters, with letters and numbers.</p>
      {state.error && <p className="form-error" role="alert">{state.error}</p>}
      {state.done && <p className="gw-ok" role="status">Password changed. Use it next time you sign in.</p>}
      <div className="ops-actions"><button className="primary-button" type="submit" disabled={state.busy}>{state.busy ? 'Changing…' : 'Change password'}</button></div>
    </form>
  );
}

export function ProfileCenter() {
  const api = useApi();
  const { user } = useAuth();
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [saved, setSaved] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    api<Preferences>('/api/v1/users/me/preferences').then(setPreferences).catch((e) => setError(errorText(e, 'Unable to load preferences.')));
    api<Activity[]>('/api/v1/users/me/activity?limit=15').then(setActivity).catch(() => setActivity([]));
  }, [api]);

  const update = async (change: Partial<Preferences>, label: string) => {
    try {
      const next = await api<Preferences>('/api/v1/users/me/preferences', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(change) });
      setPreferences(next);
      if (change.theme) applyTheme(change.theme);
      if (change.alert_min_severity || change.alert_categories) window.dispatchEvent(new Event('gridsense:alerts-changed'));
      setSaved(`${label} saved`);
      window.setTimeout(() => setSaved(''), 1800);
    } catch (e) { setError(errorText(e, 'Unable to save preferences.')); }
  };

  if (!user) return null;
  if (!preferences) return <Loading error={error} what="your profile" />;
  const pages = user.role === 'research_analyst' ? ANALYST_PAGES : Object.keys(PAGES);
  const seesAlerts = user.role !== 'research_analyst';

  return (
    <div className="gw-board">
      <div className="ops-profile-head gw-card">
        <span className="ops-avatar">{user.display_name.split(' ').map((part) => part[0]).slice(0, 2).join('').toUpperCase()}</span>
        <div><h2>{user.display_name}</h2><p>{user.email}</p><span className="dm-badge dm-normal">{roleLabels[user.role]}</span></div>
        <ul className="ops-access">{ACCESS[user.role].map((item) => <li key={item}>✓ {item}</li>)}</ul>
      </div>
      {error && <div className="gw-card gw-empty gw-bad" role="alert">{error}</div>}

      <Section title="Preferences" note="Saved to your account, so they follow you to any browser" side={saved && <span className="gw-ok">{saved}</span>}>
        <div className="gw-card ops-settings">
          <div className="ops-setting">
            <div className="ops-setting-text"><label htmlFor="landing">Start page</label><p>Where you land after signing in.</p></div>
            <div className="ops-setting-control"><select id="landing" value={preferences.landing_page} onChange={(event) => void update({ landing_page: event.target.value }, 'Start page')}>{pages.map((page) => <option key={page} value={page}>{PAGES[page]}</option>)}</select></div>
          </div>
          <div className="ops-setting">
            <div className="ops-setting-text"><label>Theme</label><p>Night (Grid Watch) or day colours.</p></div>
            <div className="ops-setting-control gw-chips gw-chips-inline">{(['dark', 'light'] as const).map((theme) => <button key={theme} type="button" className={`gw-chip${preferences.theme === theme ? ' gw-chip-active' : ''}`} onClick={() => void update({ theme }, 'Theme')}>{theme === 'dark' ? 'Night' : 'Day'}</button>)}</div>
          </div>
          {seesAlerts && <>
            <div className="ops-setting">
              <div className="ops-setting-text"><label htmlFor="severity">Alert badge severity</label><p>Only alerts at or above this severity count towards the badge in the header.</p></div>
              <div className="ops-setting-control"><select id="severity" value={preferences.alert_min_severity} onChange={(event) => void update({ alert_min_severity: event.target.value as Preferences['alert_min_severity'] }, 'Alert severity')}><option value="low">Low and above</option><option value="medium">Medium and above</option><option value="high">High and above</option><option value="critical">Critical only</option></select></div>
            </div>
            <div className="ops-setting ops-setting-wide">
              <div className="ops-setting-text"><label>Alert types I follow</label><p>Types you untick still appear on the Alerts page, but don&apos;t count towards your badge.</p></div>
              <div className="ops-checks">
                {Object.entries(CATEGORY_LABELS).filter(([key]) => key !== 'demand').map(([key, label]) => (
                  <label key={key} className="ops-check"><input type="checkbox" checked={preferences.alert_categories.includes(key)} onChange={(event) => void update({ alert_categories: event.target.checked ? [...preferences.alert_categories, key] : preferences.alert_categories.filter((item) => item !== key) }, 'Alert types')} />{label}</label>
                ))}
              </div>
            </div>
          </>}
        </div>
      </Section>

      <div className="gw-two">
        <Section title="Change password"><PasswordForm /></Section>
        <Section title="Your recent activity" note="From the audit log">
          <div className="gw-card ops-activity">
            {activity.length === 0 ? <p className="gw-muted">No recorded activity yet.</p> : activity.map((item) => (
              <div key={item.id} className="ops-activity-row"><span>{actionText(item)}</span><time title={when(item.created_at)}>{timeAgo(item.created_at)}</time></div>
            ))}
          </div>
        </Section>
      </div>
    </div>
  );
}
