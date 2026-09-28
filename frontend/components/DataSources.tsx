'use client';

import { useEffect, useState } from 'react';
import { useApi } from './api';

type Status = {
  sync_enabled: boolean;
  sldc_load: { first_slot: string | null; last_slot: string | null; rows: number; last_fetched_at: string | null };
  sldc_realtime: { snapshots: number; first: string | null; last: string | null; readings: number };
  sldc_daily_summary: { days: number; first_day: string | null; last_day: string | null };
  weather: Record<string, { first_slot: string; last_slot: string; rows: number; last_fetched_at: string }>;
  calendar: { first_day: string | null; last_day: string | null; days: number };
};

const count = (n: number) => n.toLocaleString('en-IN');
const day = (iso: string | null | undefined) => (iso ? new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—');
const stamp = (iso: string | null | undefined) => (iso ? `${day(iso)}, ${iso.slice(11, 16)}` : '—');

function years(from: string | null, to: string | null) {
  if (!from || !to) return '—';
  return `${((new Date(to.slice(0, 10)).getTime() - new Date(from.slice(0, 10)).getTime()) / (365.25 * 86400000)).toFixed(1)} years`;
}

/** Live coverage of every collected source, for the Datasets page. */
export function DataSources() {
  const api = useApi();
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { api<Status>('/api/v1/collection/status').then(setStatus).catch((e) => setError(e instanceof Error ? e.message : 'Collection status is unavailable.')); }, [api]);

  if (error) return <div className="workspace-state workspace-state-error" role="alert">{error}</div>;
  if (!status) return <div className="workspace-state">Loading data coverage…</div>;
  const load = status.sldc_load;
  const nasa = status.weather.nasa_power;
  const meteo = status.weather.open_meteo;
  const weatherRows = Object.values(status.weather).reduce((sum, source) => sum + source.rows, 0);
  const sources = [
    { name: 'Delhi load, every 5 min', origin: 'Delhi SLDC SCADA', rows: load.rows, range: `${day(load.first_slot)} → ${stamp(load.last_slot)}` },
    { name: 'Live grid snapshots', origin: 'SLDC grid-watch API + real-time page', rows: status.sldc_realtime.readings, range: `${stamp(status.sldc_realtime.first)} → ${stamp(status.sldc_realtime.last)}` },
    { name: 'Official daily peak / minimum', origin: 'SLDC load profile', rows: status.sldc_daily_summary.days, range: `${day(status.sldc_daily_summary.first_day)} → ${day(status.sldc_daily_summary.last_day)}` },
    { name: 'Hourly weather (history)', origin: 'NASA POWER', rows: nasa?.rows ?? 0, range: nasa ? `${day(nasa.first_slot)} → ${day(nasa.last_slot)}` : '—' },
    { name: 'Hourly weather (recent + 7-day forecast)', origin: 'Open-Meteo', rows: meteo?.rows ?? 0, range: meteo ? `${day(meteo.first_slot)} → ${day(meteo.last_slot)}` : '—' },
    { name: 'Holidays & festivals', origin: 'Indian gazetted + restricted lists', rows: status.calendar.days, range: `${day(status.calendar.first_day)} → ${day(status.calendar.last_day)}` },
  ];
  return (
    <>
      <div className="metric-grid">
        <article className="metric-card metric-card-blue"><p>Collected sources</p><strong>{sources.length}</strong><span>{status.sync_enabled ? 'Auto-sync on in this API' : 'Synced by the collection worker'}</span></article>
        <article className="metric-card metric-card-green"><p>Load history</p><strong>{years(load.first_slot, load.last_slot)}</strong><span>{count(load.rows)} five-minute readings</span></article>
        <article className="metric-card metric-card-amber"><p>Weather hours</p><strong>{count(weatherRows)}</strong><span>since {day(nasa?.first_slot)}</span></article>
        <article className="metric-card metric-card-slate"><p>Last load update</p><strong>{load.last_slot ? load.last_slot.slice(11, 16) : '—'}</strong><span>{day(load.last_slot)} IST</span></article>
      </div>
      <div className="data-table-panel">
        <div className="panel-heading"><div><h2>Collected data sources</h2><p>Kept up to date automatically; this is what GridSense AI trains on.</p></div><span className="panel-badge">Live</span></div>
        <div className="forecast-table-wrap"><table className="forecast-table"><thead><tr><th>Dataset</th><th>Source</th><th>Rows</th><th>Coverage</th></tr></thead>
          <tbody>{sources.map((source) => <tr key={source.name}><td><strong>{source.name}</strong></td><td>{source.origin}</td><td>{count(source.rows)}</td><td>{source.range}</td></tr>)}</tbody></table></div>
      </div>
    </>
  );
}
