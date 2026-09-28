'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import { useApi } from '../api';
import { useAuth } from '../AuthProvider';
import { Chips, errorText, inr, Loading, mw, Section, Stat, timeAgo } from './ui';

type Recommendation = {
  id: number; category: string; priority: 'low' | 'medium' | 'high' | 'critical'; action: string; expected_reduction_mw: number | null; expected_savings: number | null;
  time_window: string; confidence: number; reason: string; status: string; created_at: string; updated_at: string; generated: boolean; valid_until: string | null;
};
type Summary = { open: number; accepted: number; completed: number; rejected: number; expired: number; open_reduction_mw: number; open_savings_inr: number; accepted_savings_inr: number; by_category: Record<string, number>; settings: Record<string, number | boolean> };

const CATEGORY_LABELS: Record<string, string> = {
  peak_management: 'Peak management', power_purchase: 'Power purchase', discom_coordination: 'DISCOM coordination', maintenance: 'Maintenance', schedule_revision: 'Schedule revision',
};
const STATUS_FLOW: Record<string, { label: string; next: string; tone: string }[]> = {
  open: [{ label: 'Accept', next: 'accepted', tone: 'primary' }, { label: 'Reject', next: 'rejected', tone: 'secondary' }],
  accepted: [{ label: 'Mark done', next: 'completed', tone: 'primary' }, { label: 'Reopen', next: 'open', tone: 'secondary' }],
  rejected: [{ label: 'Reopen', next: 'open', tone: 'secondary' }],
  completed: [], expired: [],
};

function NewActionForm({ onCreated }: { onCreated: () => void }) {
  const api = useApi();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const number = (name: string) => (form.get(name) ? Number(form.get(name)) : null);
    try {
      await api('/api/v1/recommendations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        category: form.get('category'), priority: form.get('priority'), action: form.get('action'), time_window: form.get('time_window'), reason: form.get('reason'),
        expected_reduction_mw: number('reduction'), expected_savings: number('savings'), confidence: Number(form.get('confidence')) / 100,
      }) });
      setOpen(false);
      setError('');
      onCreated();
    } catch (e) { setError(errorText(e, 'Unable to create the action.')); }
  };
  if (!open) return <button className="secondary-button" type="button" onClick={() => setOpen(true)}>+ New action</button>;
  return (
    <form className="gw-card ops-form" onSubmit={(event) => void submit(event)}>
      <div className="ops-form-grid">
        <label>Category<select name="category" defaultValue="peak_management">{Object.entries(CATEGORY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Priority<select name="priority" defaultValue="medium"><option value="critical">Critical</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
        <label>Time window<input name="time_window" required maxLength={120} placeholder="Thu 17 Sep, 22:00–00:00" /></label>
        <label>Confidence (%)<input name="confidence" type="number" min={0} max={100} defaultValue={70} /></label>
        <label>Expected reduction (MW)<input name="reduction" type="number" min={0} step="1" /></label>
        <label>Expected savings (₹)<input name="savings" type="number" min={0} step="1000" /></label>
      </div>
      <label>Action<textarea name="action" required maxLength={2000} rows={2} placeholder="What should be done" /></label>
      <label>Reason<textarea name="reason" required maxLength={2000} rows={2} placeholder="Why" /></label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="ops-actions"><button className="primary-button" type="submit">Add to queue</button><button className="secondary-button" type="button" onClick={() => setOpen(false)}>Cancel</button></div>
    </form>
  );
}

export function RecommendationsBoard() {
  const api = useApi();
  const { user } = useAuth();
  const [items, setItems] = useState<Recommendation[] | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [status, setStatus] = useState('open');
  const [category, setCategory] = useState('all');
  const [expanded, setExpanded] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    const query = new URLSearchParams({ limit: '300' });
    if (status !== 'all') query.set('status', status);
    if (category !== 'all') query.set('category', category);
    try {
      const [list, counts] = await Promise.all([api<Recommendation[]>(`/api/v1/recommendations?${query}`), api<Summary>('/api/v1/recommendations/summary')]);
      setItems(list); setSummary(counts); setError('');
    } catch (e) { setError(errorText(e, 'Unable to load recommendations.')); }
  }, [api, status, category]);
  useEffect(() => { void load(); }, [load]);

  const generate = async () => {
    setBusy(true);
    try {
      const result = await api<{ created: number; refreshed: number; expired: number; enabled: boolean }>('/api/v1/recommendations/generate', { method: 'POST' });
      setMessage(result.enabled ? `Updated from the latest forecast: ${result.created} new, ${result.refreshed} refreshed, ${result.expired} expired.` : 'Automatic recommendations are switched off in Settings.');
      await load();
    } catch (e) { setError(errorText(e, 'Unable to generate recommendations.')); } finally { setBusy(false); }
  };
  const move = async (id: number, next: string) => {
    try { await api(`/api/v1/recommendations/${id}/status`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: next }) }); await load(); } catch (e) { setError(errorText(e, 'Unable to update the action.')); }
  };

  if (!items || !summary) return <Loading error={error} what="recommendations" />;
  const price = Number(summary.settings['recommendations.power_price_inr_mwh']);

  return (
    <div className="gw-board">
      <div className="dm-metrics">
        <Stat label="Open actions" value={summary.open} detail={`${summary.accepted} accepted · ${summary.completed} done · ${summary.rejected} rejected`} tone={summary.open ? 'high' : 'ok'} />
        <Stat label="Peak MW to cut" value={mw(summary.open_reduction_mw)} detail="Across open peak-management actions" />
        <Stat label="Avoidable peak cost" value={inr(summary.open_savings_inr)} detail={`At ₹${price.toLocaleString('en-IN')}/MWh (change in Settings)`} />
        <Stat label="Value of accepted actions" value={inr(summary.accepted_savings_inr)} detail="Accepted or completed" tone="ok" />
      </div>
      <Section title="Action queue" note={`Built hourly from the 7-day forecast, holiday effects and live DISCOM drawl${summary.settings['recommendations.enabled'] ? '' : ' (automatic generation is off)'}`}
        side={<div className="ops-actions">
          {user?.role !== 'research_analyst' && <button className="primary-button" type="button" disabled={busy} onClick={() => void generate()}>{busy ? 'Updating…' : 'Update from forecast'}</button>}
          <NewActionForm onCreated={() => void load()} />
        </div>}>
        {message && <p className="gw-footnote gw-ok">{message}</p>}
        <div className="ops-filters">
          <Chips label="Status" value={status} onChange={setStatus} options={[{ value: 'open', label: 'Open', count: summary.open }, { value: 'accepted', label: 'Accepted', count: summary.accepted || undefined }, { value: 'completed', label: 'Done' }, { value: 'rejected', label: 'Rejected' }, { value: 'expired', label: 'Expired' }, { value: 'all', label: 'All' }]} />
          <Chips label="Category" value={category} onChange={setCategory} options={[{ value: 'all', label: 'All types' }, ...Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ value, label, count: summary.by_category[value] || undefined }))]} />
        </div>
        {error && <div className="gw-card gw-empty gw-bad" role="alert">{error}</div>}
        {items.length === 0 ? <div className="gw-card gw-empty">No actions here. {status === 'open' ? 'Nothing needs doing for the coming week.' : ''}</div> : (
          <div className="ops-rec-list">
            {items.map((item) => (
              <article key={item.id} className={`gw-card ops-rec ops-sev-${item.priority}`}>
                <div className="ops-rec-head">
                  <span className={`dm-badge ops-badge-${item.priority}`}>{item.priority}</span>
                  <span className="ops-alert-cat">{CATEGORY_LABELS[item.category] ?? item.category.replaceAll('_', ' ')}</span>
                  <span className="ops-alert-cat">{item.generated ? 'Generated' : 'Manual'}</span>
                  <span className={`ops-status ops-status-${item.status}`}>{item.status}</span>
                </div>
                <p className="ops-rec-action">{item.action}</p>
                <div className="dm-facts">
                  <span>When <b>{item.time_window}</b></span>
                  {item.expected_reduction_mw != null && <span>Reduction <b>{mw(item.expected_reduction_mw)}</b></span>}
                  {item.expected_savings != null && <span>Value <b>{inr(item.expected_savings)}</b></span>}
                  <span>Confidence <b>{Math.round(item.confidence * 100)}%</b></span>
                  <span>Updated <b>{timeAgo(item.updated_at)}</b></span>
                </div>
                <button type="button" className="ops-why" onClick={() => setExpanded(expanded === item.id ? null : item.id)} aria-expanded={expanded === item.id}>{expanded === item.id ? 'Hide why' : 'Why?'}</button>
                {expanded === item.id && <p className="ops-rec-reason">{item.reason}</p>}
                {STATUS_FLOW[item.status]?.length > 0 && (
                  <div className="ops-actions">{STATUS_FLOW[item.status].map((step) => <button key={step.next} type="button" className={`${step.tone}-button`} onClick={() => void move(item.id, step.next)}>{step.label}</button>)}</div>
                )}
              </article>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
