'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { useApi } from '../api';
import { Chips, errorText, Loading, Section, Stat, timeAgo, when } from './ui';

type Alert = { id: number; title: string; severity: 'low' | 'medium' | 'high' | 'critical'; message: string; acknowledged: boolean; created_at: string; category: string; resolved_at: string | null; acknowledged_by: string | null; link: string | null };
export type AlertSummary = { active: number; unacknowledged: number; for_me: number; critical: number; by_severity: Record<string, number>; by_category: Record<string, number>; resolved_last_24h: number };

export const CATEGORY_LABELS: Record<string, string> = {
  demand_forecast: 'Demand forecast', demand: 'Demand forecast', low_demand: 'Low demand', live_load: 'Live load', frequency: 'Frequency',
  overdrawal: 'Overdrawal', voltage: 'Voltage', peak_record: 'Peak record', data_quality: 'Data quality',
};
const CATEGORY_ICONS: Record<string, string> = {
  demand_forecast: '📈', demand: '📈', low_demand: '📉', live_load: '⚡', frequency: '〰', overdrawal: '⇪', voltage: '🔌', peak_record: '🏔', data_quality: '🛰',
};

type StateFilter = 'active' | 'resolved' | 'all';

/** Polls the alert summary for the header badge. */
export function useAlertSummary(enabled: boolean) {
  const api = useApi();
  const [summary, setSummary] = useState<AlertSummary | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const load = () => api<AlertSummary>('/api/v1/alerts/summary').then((data) => !cancelled && setSummary(data)).catch(() => undefined);
    void load();
    const id = window.setInterval(load, 60_000);
    const refresh = () => void load();
    window.addEventListener('gridsense:alerts-changed', refresh);
    return () => { cancelled = true; window.clearInterval(id); window.removeEventListener('gridsense:alerts-changed', refresh); };
  }, [api, enabled]);
  return summary;
}

export function AlertsCenter() {
  const api = useApi();
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [summary, setSummary] = useState<AlertSummary | null>(null);
  const [state, setState] = useState<StateFilter>('active');
  const [category, setCategory] = useState('all');
  const [severity, setSeverity] = useState('all');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  const load = useCallback(async () => {
    const query = new URLSearchParams({ state, limit: '200' });
    if (category !== 'all') query.set('category', category);
    if (severity !== 'all') query.set('severity', severity);
    try {
      const [items, counts] = await Promise.all([api<Alert[]>(`/api/v1/alerts?${query}`), api<AlertSummary>('/api/v1/alerts/summary')]);
      setAlerts(items);
      setSummary(counts);
      setError('');
    } catch (e) {
      setError(errorText(e, 'Unable to load alerts.'));
    }
  }, [api, state, category, severity]);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(id);
  }, [load]);

  const changed = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label);
    try { await action(); await load(); window.dispatchEvent(new Event('gridsense:alerts-changed')); } catch (e) { setError(errorText(e, 'The alert action failed.')); } finally { setBusy(''); }
  };

  if (!alerts || !summary) return <Loading error={error} what="alerts" />;
  const categories = Object.keys(CATEGORY_LABELS).filter((key) => key !== 'demand');

  return (
    <div className="gw-board">
      <div className="dm-metrics">
        <Stat label="Needs attention" value={summary.unacknowledged} detail={`${summary.active} active in total`} tone={summary.unacknowledged ? 'high' : 'ok'} />
        <Stat label="Critical" value={summary.critical} detail={`${summary.by_severity.high ?? 0} high · ${summary.by_severity.medium ?? 0} medium · ${summary.by_severity.low ?? 0} low`} tone={summary.critical ? 'critical' : 'muted'} />
        <Stat label="Matching your preferences" value={summary.for_me} detail={<Link href="/profile">Change alert preferences →</Link>} />
        <Stat label="Resolved in 24 h" value={summary.resolved_last_24h} detail="Cleared automatically when conditions returned to normal" tone="ok" />
      </div>

      <Section title="Alert centre" note="Checked every 5 minutes against live SLDC data, the 7-day forecast and your settings"
        side={<div className="ops-actions">
          <button className="secondary-button" type="button" disabled={!!busy} onClick={() => void changed('evaluate', () => api('/api/v1/alerts/evaluate', { method: 'POST' }))}>{busy === 'evaluate' ? 'Checking…' : 'Check now'}</button>
          <button className="primary-button" type="button" disabled={!!busy || !summary.unacknowledged} onClick={() => void changed('ack-all', () => api(`/api/v1/alerts/acknowledge-all${category !== 'all' ? `?category=${category}` : ''}`, { method: 'POST' }))}>Acknowledge {category !== 'all' ? CATEGORY_LABELS[category].toLowerCase() : 'all'}</button>
        </div>}>
        <div className="ops-filters">
          <Chips<StateFilter> label="Status" value={state} onChange={setState} options={[{ value: 'active', label: 'Active', count: summary.active }, { value: 'resolved', label: 'Resolved' }, { value: 'all', label: 'All' }]} />
          <Chips label="Severity" value={severity} onChange={setSeverity} options={[{ value: 'all', label: 'Any severity' }, ...(['critical', 'high', 'medium', 'low'] as const).map((level) => ({ value: level, label: level[0].toUpperCase() + level.slice(1), count: summary.by_severity[level] || undefined }))]} />
          <Chips label="Category" value={category} onChange={setCategory} options={[{ value: 'all', label: 'All types' }, ...categories.map((key) => ({ value: key, label: CATEGORY_LABELS[key], count: summary.by_category[key] || undefined }))]} />
        </div>
        {error && <div className="gw-card gw-empty gw-bad" role="alert">{error}</div>}
        {alerts.length === 0 ? <div className="gw-card gw-empty">{state === 'active' ? 'All clear. No active alerts match these filters.' : 'No alerts match these filters.'}</div> : (
          <div className="ops-alert-list">
            {alerts.map((alert) => (
              <article key={alert.id} className={`gw-card ops-alert ops-sev-${alert.severity}${alert.resolved_at ? ' ops-alert-resolved' : ''}${alert.acknowledged ? ' ops-alert-acked' : ''}`}>
                <span className="ops-alert-icon" aria-hidden="true">{CATEGORY_ICONS[alert.category] ?? '•'}</span>
                <div className="ops-alert-body">
                  <div className="ops-alert-head">
                    <h3>{alert.title}</h3>
                    <span className={`dm-badge ops-badge-${alert.severity}`}>{alert.severity}</span>
                    <span className="ops-alert-cat">{CATEGORY_LABELS[alert.category] ?? alert.category}</span>
                  </div>
                  <p>{alert.message}</p>
                  <div className="ops-alert-meta">
                    <span title={when(alert.created_at)}>Raised {timeAgo(alert.created_at)}</span>
                    {alert.resolved_at && <span className="gw-ok">Resolved {timeAgo(alert.resolved_at)}</span>}
                    {alert.acknowledged && <span>Acknowledged{alert.acknowledged_by ? ` by ${alert.acknowledged_by}` : ''}</span>}
                    {alert.link && <Link href={alert.link}>Open {alert.link.replace('/', '').replace('-', ' ')} →</Link>}
                  </div>
                </div>
                {!alert.acknowledged && !alert.resolved_at && (
                  <button className="secondary-button ops-ack" type="button" disabled={!!busy} onClick={() => void changed(`ack-${alert.id}`, () => api(`/api/v1/alerts/${alert.id}/acknowledge`, { method: 'POST' }))}>Acknowledge</button>
                )}
              </article>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
