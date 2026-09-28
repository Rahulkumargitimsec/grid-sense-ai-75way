'use client';

import { FormEvent, useEffect, useRef, useState } from 'react';
import { useApi } from './api';
import { useAuth } from './AuthProvider';

type AdvancedKind = 'explanation' | 'reports' | 'research' | 'admin' | 'settings';
type Explanation = { model_name: string; forecast_for: string; predicted_demand_mw: number; feature_contributions: Record<string, number>; explanation: string };
type Report = { id: number; type: string; format: string; status: string; generated_by: string; created_at: string; content: string };
type Experiment = { id: number; name: string; dataset_version: string | null; parameters: Record<string, string | number | boolean>; status: string; created_at: string; results: { model_name: string; metrics: Record<string, number>; reproducibility: Record<string, string | number | boolean> }[] };
type AdminUser = { id: string; email: string; display_name: string; role: string; is_active: boolean };
type AuditLog = { id: number; actor_id: string | null; action: string; resource_type: string; resource_id: string | null; details: Record<string, unknown>; created_at: string };
type Setting = { key: string; value: string; updated_at: string };

async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { Authorization: `Bearer ${token}`, ...(init?.headers || {}) } });
  if (!response.ok) { const payload = await response.json().catch(() => null); throw new Error(typeof payload?.detail === 'string' ? payload.detail : 'The request could not be completed.'); }
  return response.json() as Promise<T>;
}

function formatDate(value: string) { return new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }); }
function ErrorState({ error }: { error: string }) { return error ? <p className="form-error" role="alert">{error}</p> : null; }

function ExplanationWorkspace() {
  const api = useApi();
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const started = useRef(false);

  const generate = async () => {
    setBusy(true);
    try {
      setExplanation(await api<Explanation>('/api/v1/explanations/forecast', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model_name: 'gridsense_ai', horizon: 1 }) }));
      setError('');
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Unable to explain the latest forecast.');
    } finally {
      setBusy(false);
    }
  };

  // Show the most recent saved explanation; only create one if none exist yet.
  // The ref stops React StrictMode's double effect from saving two predictions.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api<Explanation[]>('/api/v1/explanations?limit=1')
      .then((items) => {
        if (items.length === 0) return generate();
        setExplanation(items[0]);
        setError('');
      })
      .catch((requestError) => setError(requestError instanceof Error ? requestError.message : 'Unable to load explanations.'));
  }, [api]);

  const contributions = explanation ? Object.entries(explanation.feature_contributions) : [];
  const baseline = explanation ? explanation.predicted_demand_mw - contributions.reduce((sum, [, value]) => sum + value, 0) : 0;
  return <>
    <div className="workspace-toolbar"><button className="primary-button" type="button" disabled={busy} onClick={() => void generate()}>{busy ? 'Explaining…' : 'Explain latest forecast'}</button></div>
    <ErrorState error={error} />
    {!explanation && !error && <div className="workspace-state">Loading the latest explanation…</div>}
    {explanation && <>
      <div className="explanation-summary"><p className="eyebrow">Natural-language explanation</p><p>{explanation.explanation}</p><span>{explanation.model_name === 'gridsense_ai' ? 'GridSense AI' : explanation.model_name.replaceAll('_', ' ')} · {formatDate(explanation.forecast_for)}</span></div>
      <p className="explanation-baseline">Starting point: {explanation.model_name === 'gridsense_ai' ? 'a normal hour (typical weather, ordinary weekday, last year\'s demand level)' : 'the latest observed load'} of {baseline.toFixed(1)} MW. Each card shows how many MW that signal adds or removes to reach {explanation.predicted_demand_mw.toFixed(1)} MW.</p>
      <div className="contribution-grid">{contributions.map(([feature, value]) => <article className="contribution-card" key={feature}><span>{feature.replaceAll('_', ' ')}</span><strong>{value > 0 ? '+' : ''}{value.toFixed(2)} MW</strong><small>contribution to forecast</small></article>)}</div>
    </>}
  </>;
}

function ReportsWorkspace({ token }: { token: string }) {
  const [reports, setReports] = useState<Report[]>([]);
  const [type, setType] = useState('forecast');
  const [format, setFormat] = useState('json');
  const [error, setError] = useState('');
  const load = async () => { try { setReports(await request<Report[]>('/api/v1/reports', token)); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : 'Unable to load reports.'); } };
  useEffect(() => { void load(); }, [token]);
  const generate = async () => { try { await request('/api/v1/reports/generate', token, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type, format, horizon: 24, model_name: 'weighted_ensemble' }) }); await load(); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : 'Unable to generate report.'); } };
  const exportReport = async (report: Report) => { try { const response = await fetch(`/api/v1/reports/${report.id}/export`, { headers: { Authorization: `Bearer ${token}` } }); const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `gridsense-report-${report.id}.${report.format}`; link.click(); URL.revokeObjectURL(url); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : 'Unable to export report.'); } };
  return <><div className="workspace-toolbar"><label>Report type<select value={type} onChange={(event) => setType(event.target.value)}><option value="forecast">Forecast</option><option value="recommendations">Recommendations</option><option value="model_comparison">Model comparison</option><option value="audit">Audit</option></select></label><label>Format<select value={format} onChange={(event) => setFormat(event.target.value)}><option value="json">JSON</option><option value="csv">CSV</option></select></label><button className="primary-button" type="button" onClick={() => void generate()}>Generate report</button></div><ErrorState error={error} /><div className="report-list">{reports.map((report) => <article className="report-row" key={report.id}><div><strong>{report.type.replaceAll('_', ' ')}</strong><span>{formatDate(report.created_at)} · {report.format.toUpperCase()} · {report.status}</span></div><button className="secondary-button" type="button" onClick={() => void exportReport(report)}>Export</button></article>)}</div></>;
}

function ResearchWorkspace({ token }: { token: string }) {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [name, setName] = useState('Baseline model comparison');
  const [error, setError] = useState('');
  const load = async () => { try { setExperiments(await request<Experiment[]>('/api/v1/research/experiments', token)); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : 'Unable to load experiments.'); } };
  useEffect(() => { void load(); }, [token]);
  const create = async (event: FormEvent) => { event.preventDefault(); try { await request('/api/v1/research/experiments', token, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, parameters: { evaluation: 'baseline_comparison' } }) }); await load(); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : 'Unable to create experiment.'); } };
  const run = async (id: number) => { try { await request(`/api/v1/research/experiments/${id}/run`, token, { method: 'POST' }); await load(); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : 'Unable to run experiment.'); } };
  return <><form className="workspace-toolbar" onSubmit={create}><label>Experiment name<input value={name} onChange={(event) => setName(event.target.value)} /></label><button className="primary-button" type="submit">Create experiment</button></form><ErrorState error={error} /><div className="experiment-list">{experiments.map((experiment) => <article className="experiment-card" key={experiment.id}><div className="recommendation-heading"><div><h3>{experiment.name}</h3><span>{experiment.status} · {formatDate(experiment.created_at)}</span></div><button className="secondary-button" type="button" onClick={() => void run(experiment.id)}>Run comparison</button></div>{experiment.results.length > 0 && <div className="experiment-results">{experiment.results.map((result) => <span key={result.model_name}><strong>{result.model_name.replace('_', ' ')}</strong><small>MAPE {result.metrics.mape?.toFixed(2)}%</small></span>)}</div>}</article>)}</div></>;
}

function AdminWorkspace({ token, settingsOnly }: { token: string; settingsOnly?: boolean }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [settings, setSettings] = useState<Setting[]>([]);
  const [error, setError] = useState('');
  const load = async () => { try { if (!settingsOnly) { setUsers(await request<AdminUser[]>('/api/v1/admin/users', token)); setLogs(await request<AuditLog[]>('/api/v1/admin/audit-logs', token)); } setSettings(await request<Setting[]>('/api/v1/admin/settings', token)); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : 'Unable to load administration data.'); } };
  useEffect(() => { void load(); }, [token, settingsOnly]);
  const toggle = async (user: AdminUser) => { try { await request(`/api/v1/admin/users/${user.id}/active?active=${!user.is_active}`, token, { method: 'PATCH' }); await load(); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : 'Unable to update user.'); } };
  const updateSetting = async (setting: Setting, value: string) => { try { await request(`/api/v1/admin/settings/${encodeURIComponent(setting.key)}`, token, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }); await load(); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : 'Unable to update setting.'); } };
  return <><ErrorState error={error} />{!settingsOnly && <><div className="admin-section"><div className="panel-heading"><div><h2>User access</h2><p>Active state is enforced by backend authentication.</p></div></div><div className="admin-user-list">{users.map((user) => <div className="admin-user-row" key={user.id}><div><strong>{user.display_name}</strong><span>{user.email} · {user.role}</span></div><button className="secondary-button" type="button" onClick={() => void toggle(user)}>{user.is_active ? 'Deactivate' : 'Activate'}</button></div>)}</div></div><div className="admin-section"><div className="panel-heading"><div><h2>Audit activity</h2><p>Recent security and operational changes.</p></div></div><div className="audit-list">{logs.slice(0, 8).map((log) => <div className="audit-row" key={log.id}><strong>{log.action.replaceAll('_', ' ')}</strong><span>{log.resource_type} · {formatDate(log.created_at)}</span></div>)}</div></div></>}{<div className="admin-section"><div className="panel-heading"><div><h2>System settings</h2><p>Only Super Admins can change these values.</p></div></div><div className="settings-list">{settings.length === 0 && <p className="workspace-muted">No settings have been persisted yet.</p>}{settings.map((setting) => <div className="setting-row" key={setting.key}><label>{setting.key}<input defaultValue={setting.value} onBlur={(event) => void updateSetting(setting, event.target.value)} /></label></div>)}</div></div>}</>;
}

export function AdvancedWorkspace({ kind }: { kind: AdvancedKind }) {
  const { accessToken } = useAuth();
  if (!accessToken) return <div className="workspace-state">Waiting for an authenticated session...</div>;
  if (kind === 'explanation') return <ExplanationWorkspace />;
  if (kind === 'reports') return <ReportsWorkspace token={accessToken} />;
  if (kind === 'research') return <ResearchWorkspace token={accessToken} />;
  return <AdminWorkspace token={accessToken} settingsOnly={kind === 'settings'} />;
}
