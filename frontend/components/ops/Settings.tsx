'use client';

import { useEffect, useState } from 'react';
import { useApi } from '../api';
import { errorText, Loading, mw, Section, timeAgo } from './ui';

type Setting = {
  key: string; group: string; label: string; kind: 'number' | 'boolean'; unit: string; help: string; minimum: number | null; maximum: number | null;
  optional: boolean; default: number | boolean | null; value: number | boolean | null; is_default: boolean; updated_by: string | null; updated_at: string | null;
};
type Thresholds = { high_mw: number; critical_mw: number; low_mw: number; high_mw_automatic: number; critical_mw_automatic: number; low_mw_automatic: number };
type Response = { can_edit: boolean; settings: Setting[]; demand_thresholds: Thresholds | null };

const GROUP_NOTES: Record<string, string> = {
  'Demand thresholds': 'What counts as high, critical and low demand in forecasts, alerts and recommendations.',
  Alerts: 'When the alert engine raises operator alerts (checked every 5 minutes).',
  Recommendations: 'How actions and their value are worked out.',
};

function SettingRow({ setting, canEdit, automatic, onSave, onReset }: { setting: Setting; canEdit: boolean; automatic?: number; onSave: (key: string, value: string | boolean) => Promise<void>; onReset: (key: string) => Promise<void> }) {
  const [draft, setDraft] = useState(setting.value == null ? '' : String(setting.value));
  const [state, setState] = useState<'idle' | 'saving' | 'saved'>('idle');
  useEffect(() => { setDraft(setting.value == null ? '' : String(setting.value)); }, [setting.value]);
  const dirty = setting.kind === 'number' && draft !== (setting.value == null ? '' : String(setting.value));

  const save = async (value: string | boolean) => {
    setState('saving');
    try { await onSave(setting.key, value); setState('saved'); window.setTimeout(() => setState('idle'), 1500); } catch { setState('idle'); }
  };

  return (
    <div className="ops-setting">
      <div className="ops-setting-text">
        <label htmlFor={setting.key}>{setting.label}</label>
        <p>{setting.help}</p>
        <span className="ops-setting-meta">
          {setting.is_default ? (setting.optional && automatic != null ? `Automatic: ${mw(automatic)}` : `Default${setting.default != null ? `: ${setting.default}${setting.unit ? ` ${setting.unit}` : ''}` : ''}`) : `Changed ${timeAgo(setting.updated_at)}`}
        </span>
      </div>
      <div className="ops-setting-control">
        {setting.kind === 'boolean' ? (
          <button id={setting.key} type="button" role="switch" aria-checked={Boolean(setting.value)} disabled={!canEdit || state === 'saving'} className={`ops-switch${setting.value ? ' ops-switch-on' : ''}`} onClick={() => void save(!setting.value)}><span /></button>
        ) : (
          <div className="ops-number">
            <input id={setting.key} type="number" step="any" min={setting.minimum ?? undefined} max={setting.maximum ?? undefined} value={draft} disabled={!canEdit}
              placeholder={setting.optional ? (automatic != null ? `Auto (${Math.round(automatic)})` : 'Automatic') : undefined}
              onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && dirty) void save(draft); }} />
            {setting.unit && <span>{setting.unit}</span>}
          </div>
        )}
        {canEdit && dirty && <button className="primary-button" type="button" disabled={state === 'saving'} onClick={() => void save(draft)}>Save</button>}
        {canEdit && !setting.is_default && !dirty && <button className="secondary-button" type="button" onClick={() => void onReset(setting.key)}>Reset</button>}
        {state === 'saved' && <span className="gw-ok ops-saved">Saved</span>}
      </div>
    </div>
  );
}

export function OperationalSettings() {
  const api = useApi();
  const [data, setData] = useState<Response | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { api<Response>('/api/v1/settings/operational').then(setData).catch((e) => setError(errorText(e, 'Unable to load settings.'))); }, [api]);

  const save = async (key: string, value: string | boolean) => {
    try {
      setData(await api<Response>(`/api/v1/settings/operational/${encodeURIComponent(key)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: value === '' ? null : value }) }));
      setError('');
    } catch (e) { setError(errorText(e, 'Unable to save the setting.')); throw e; }
  };
  const reset = async (key: string) => {
    try { setData(await api<Response>(`/api/v1/settings/operational/${encodeURIComponent(key)}`, { method: 'DELETE' })); setError(''); } catch (e) { setError(errorText(e, 'Unable to reset the setting.')); }
  };

  if (!data) return <Loading error={error} what="settings" />;
  const groups = data.settings.map((setting) => setting.group).filter((group, index, all) => all.indexOf(group) === index);
  const automatic: Record<string, number | undefined> = data.demand_thresholds ? { 'demand.high_mw': data.demand_thresholds.high_mw_automatic, 'demand.critical_mw': data.demand_thresholds.critical_mw_automatic, 'demand.low_mw': data.demand_thresholds.low_mw_automatic } : {};

  return (
    <div className="gw-board">
      {!data.can_edit && <p className="gw-card gw-pad gw-footnote">You can view these settings. Only a Super Admin can change them.</p>}
      {error && <div className="gw-card gw-empty gw-bad" role="alert">{error}</div>}
      {data.demand_thresholds && (
        <div className="dm-metrics">
          <div className="gw-card dm-metric"><p>High mark in use</p><strong>{mw(data.demand_thresholds.high_mw)}</strong><span>automatic {mw(data.demand_thresholds.high_mw_automatic)}</span></div>
          <div className="gw-card dm-metric"><p>Critical mark in use</p><strong>{mw(data.demand_thresholds.critical_mw)}</strong><span>automatic {mw(data.demand_thresholds.critical_mw_automatic)}</span></div>
          <div className="gw-card dm-metric"><p>Low mark in use</p><strong>{mw(data.demand_thresholds.low_mw)}</strong><span>automatic {mw(data.demand_thresholds.low_mw_automatic)}</span></div>
          <div className="gw-card dm-metric"><p>Customised settings</p><strong>{data.settings.filter((setting) => !setting.is_default).length} / {data.settings.length}</strong><span>the rest use defaults</span></div>
        </div>
      )}
      {groups.map((group) => (
        <Section key={group} title={group} note={GROUP_NOTES[group]}>
          <div className="gw-card ops-settings">
            {data.settings.filter((setting) => setting.group === group).map((setting) => <SettingRow key={setting.key} setting={setting} canEdit={data.can_edit} automatic={automatic[setting.key]} onSave={save} onReset={reset} />)}
          </div>
        </Section>
      ))}
    </div>
  );
}
