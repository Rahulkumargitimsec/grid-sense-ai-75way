'use client';

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { useApi } from '../api';
import { FrequencyGauge, LineSeries, MultiLineChart, PeakWeatherChart, PeakWeatherDay, ScheduleActualBars } from './GridCharts';

/* ---- API shapes (backend/app/services/grid_live.py) ---------------------------------------------- */

type Reading = { entity: string; schedule_mw: number | null; actual_mw: number | null; deviation_mw: number | null; mvar: number | null; voltage_kv: number | null; load_mw: number | null; status: number | null };
type Discom = Reading & { name: string; share_pct: number | null };
type Snapshot = {
  captured_at: string; source_time: string | null; load_mw: number | null; schedule_mw: number | null; drawal_mw: number | null; odud_mw: number | null;
  frequency_hz: number | null; delhi_generation_mw: number | null; peak_today_mw: number | null; peak_today_time: string | null;
  min_today_mw: number | null; min_today_time: string | null; all_time_peak_mw: number | null; all_time_peak_at: string | null;
};
type FyPeak = { financial_year: string; peak_mw: number; peak_at: string | null; precision: string };
export type LiveState = {
  server_time: string; snapshot: Snapshot | null; vs_yesterday: { yesterday_mw: number; change_pct: number } | null; readings_captured_at: string | null;
  discoms: Discom[]; substations: Reading[]; delhi_generation: Reading[]; central_generation: Reading[]; states: Reading[]; imports: Reading[]; exports: Reading[];
  financial_year_peaks: FyPeak[]; all_time_peak: { peak_mw: number; peak_at: string | null } | null;
};
type Transformer = { transformer: string; mw: number | null; mvar: number | null; voltage_kv: number | null };
type Curve = { date: string; times: string[]; series: Record<string, (number | null)[]> };

/* ---- formatting: backend timestamps are naive IST, so never let the browser shift them ------------ */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'June', 'July', 'Aug', 'Sept', 'Oct', 'Nov', 'Dec'];

function parts(iso: string | null | undefined) {
  const m = iso?.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/);
  return m ? { y: m[1], mo: Number(m[2]), d: Number(m[3]), h: m[4], mi: m[5], s: m[6] ?? '00' } : null;
}

export function asOf(iso: string | null | undefined, seconds = false) {
  const p = parts(iso);
  return p ? `${p.d} ${MONTHS[p.mo - 1]} ${p.y}, ${p.h}:${p.mi}${seconds ? `:${p.s}` : ''}` : '—';
}

function dmy(iso: string | null | undefined) {
  const p = parts(iso);
  return p ? `${String(p.d).padStart(2, '0')}/${String(p.mo).padStart(2, '0')}/${p.y}, ${p.h}:${p.mi}:${p.s}` : '—';
}

const mw = (v: number | null | undefined) => (v == null ? '—' : Math.round(v).toLocaleString('en-IN'));
const signed = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(Math.round(v)).toLocaleString('en-IN')}`);

function todayIst() {
  return new Date(Date.now() + (330 + new Date().getTimezoneOffset()) * 60000).toISOString().slice(0, 10);
}

/* ---- data hook ------------------------------------------------------------------------------------ */

export function useLiveGrid(intervalMs = 60_000) {
  const api = useApi();
  const [data, setData] = useState<LiveState | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(() => api<LiveState>('/api/v1/grid/live').then((next) => { setData(next); setError(''); }).catch((e) => setError(e instanceof Error ? e.message : 'Live grid data is unavailable.')), [api]);

  useEffect(() => {
    void load();
    // Align refreshes to the top of the minute, like the SCADA screens operators already watch.
    let interval: number | undefined;
    const first = window.setTimeout(() => { void load(); interval = window.setInterval(() => void load(), intervalMs); }, intervalMs - (Date.now() % intervalMs) + 1500);
    return () => { window.clearTimeout(first); if (interval) window.clearInterval(interval); };
  }, [load, intervalMs]);

  return { data, error, reload: load };
}

export function LiveClock() {
  const [now, setNow] = useState('--:--:--');
  useEffect(() => {
    const tick = () => setNow(new Date().toLocaleTimeString('en-IN', { hour12: false, timeZone: 'Asia/Kolkata' }));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, []);
  return <span className="gw-live-pill"><i className="gw-live-dot" />Live · {now}</span>;
}

/* ---- sections ------------------------------------------------------------------------------------- */

function SectionTitle({ title, stamp, note, children }: { title: string; stamp?: string; note?: string; children?: React.ReactNode }) {
  return (
    <div className="gw-section-head">
      <div><h2>{title}</h2>{stamp && <p className="gw-stamp">As of {stamp} IST</p>}</div>
      <div className="gw-section-side">{note && <p>{note}</p>}{children}</div>
    </div>
  );
}

function Stat({ label, value, unit = 'MW', tone, foot, footTone }: { label: string; value: string; unit?: string; tone?: 'ok' | 'bad'; foot?: React.ReactNode; footTone?: 'red' | 'ok' | 'bad' }) {
  return (
    <div className="gw-stat">
      <p>{label}</p>
      <strong className={tone ? `gw-${tone}` : undefined}>{value}<small>{unit}</small></strong>
      {foot && <span className={footTone ? `gw-foot-${footTone}` : 'gw-foot'}>{foot}</span>}
    </div>
  );
}

export function FrequencyCard({ data }: { data: LiveState }) {
  return (
    <section className="gw-card gw-frequency">
      <div className="gw-card-head"><p>Current frequency</p><span className="gw-tag">IEGC band</span></div>
      <FrequencyGauge hz={data.snapshot?.frequency_hz} />
    </section>
  );
}

export function DemandSnapshot({ data }: { data: LiveState }) {
  const s = data.snapshot;
  const current = parts(data.server_time);
  const currentFy = current ? (current.mo >= 4 ? Number(current.y) : Number(current.y) - 1) : null;
  // Show the two most recent financial years before the current one, like SLDC's own board.
  const fy = data.financial_year_peaks.filter((row) => !currentFy || Number(row.financial_year.slice(3, 7)) < currentFy).slice(-2);
  const change = data.vs_yesterday?.change_pct;
  return (
    <section className="gw-card gw-snapshot">
      <div className="gw-card-head"><p>Delhi demand snapshot as on {asOf(s?.source_time ?? s?.captured_at, true)}</p><span className="gw-tag">MW</span></div>
      <div className="gw-stat-grid">
        <Stat label="Schedule" value={mw(s?.schedule_mw)} />
        <Stat label="Drawl" value={mw(s?.drawal_mw)} />
        <Stat label="OD/UD" value={signed(s?.odud_mw)} tone={s?.odud_mw != null ? (s.odud_mw > 0 ? 'bad' : 'ok') : undefined} foot={s?.odud_mw != null ? (s.odud_mw > 0 ? 'Overdrawing vs schedule' : 'Underdrawing vs schedule') : undefined} />
      </div>
      <div className="gw-divider" />
      <div className="gw-stat-grid">
        <Stat label="Current demand" value={mw(s?.load_mw)} foot={change != null ? <span className={change >= 0 ? 'gw-up' : 'gw-down'}>{change >= 0 ? '▲' : '▼'} {Math.abs(change).toFixed(2)}% {change >= 0 ? 'higher' : 'lower'} than this time yesterday</span> : undefined} />
        <Stat label="Today's peak load" value={mw(s?.peak_today_mw)} foot={s?.peak_today_time ? `Today so far ${s.peak_today_time}` : undefined} footTone="red" />
        <Stat label="Today's off-peak load" value={mw(s?.min_today_mw)} foot={s?.min_today_time ? `Today so far ${s.min_today_time}` : undefined} footTone="red" />
      </div>
      <div className="gw-divider" />
      <div className="gw-stat-grid">
        {fy.map((row) => <Stat key={row.financial_year} label={`${row.financial_year} peak`} value={mw(row.peak_mw)} foot={dmy(row.peak_at)} footTone="red" />)}
        {data.all_time_peak && <Stat label="All-time peak" value={mw(data.all_time_peak.peak_mw)} foot={asOf(data.all_time_peak.peak_at, true)} footTone="red" />}
      </div>
    </section>
  );
}

const DISCOM_LABEL: Record<string, string> = { NDPL: 'TPDDL' };

export function DiscomDrawl({ data }: { data: LiveState }) {
  return (
    <section className="gw-section">
      <SectionTitle title="Discom drawl" stamp={asOf(data.readings_captured_at)} note="Live load share across Delhi's distribution utilities" />
      <div className="gw-discoms">
        {data.discoms.map((d) => (
          <article className="gw-card gw-discom" key={d.entity}>
            <h3>{DISCOM_LABEL[d.entity] ?? d.entity}</h3>
            <p className="gw-discom-name">{d.name}</p>
            <strong>{mw(d.actual_mw)}<small>MW</small></strong>
            <p className="gw-discom-share">{d.share_pct ?? '—'}% of Delhi load</p>
            <p className={`gw-discom-dev ${(d.deviation_mw ?? 0) > 0 ? 'gw-bad' : 'gw-ok'}`}>Schedule {mw(d.schedule_mw)} · OD/UD {signed(d.deviation_mw)}</p>
            <span className="gw-share-bar"><i style={{ width: `${Math.min(100, d.share_pct ?? 0)}%` }} /></span>
          </article>
        ))}
      </div>
    </section>
  );
}

export function TransmissionStatus({ data }: { data: LiveState }) {
  const api = useApi();
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<Record<string, Transformer[] | 'loading' | 'error'>>({});
  const rows = data.substations.filter((row) => row.entity.toLowerCase().includes(query.trim().toLowerCase()));
  const totalMw = data.substations.reduce((sum, row) => sum + (row.actual_mw ?? 0), 0);
  const suspect = data.substations.filter((row) => row.status !== 1).length;

  const toggle = (name: string) => {
    const next = open === name ? null : name;
    setOpen(next);
    if (next && !Array.isArray(detail[next])) {
      setDetail((d) => ({ ...d, [next]: 'loading' }));
      api<Transformer[]>(`/api/v1/grid/substations/${encodeURIComponent(next)}/transformers`)
        .then((items) => setDetail((d) => ({ ...d, [next]: items })))
        .catch(() => setDetail((d) => ({ ...d, [next]: 'error' })));
    }
  };

  return (
    <section className="gw-section">
      <SectionTitle title="DTL transmission network status" stamp={asOf(data.readings_captured_at)} note="Loading on key 400kV / 220kV grid substations">
        <label className="gw-search"><span aria-hidden="true">⌕</span><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search substation..." aria-label="Search substation" /></label>
      </SectionTitle>
      <div className="gw-card gw-table-card">
        <div className="gw-table-summary"><span>{data.substations.length} substations</span><span>{mw(totalMw)} MW total</span><span className={suspect ? 'gw-bad' : 'gw-ok'}>{suspect} RTU suspect</span></div>
        <div className="gw-table-scroll">
          <table className="gw-table">
            <thead><tr><th /><th>Substation</th><th>RTU*</th><th>MW</th><th>MVAR</th><th>Voltage (kV)</th></tr></thead>
            <tbody>
              {rows.map((row) => {
                const items = detail[row.entity];
                return (
                  <Fragment key={row.entity}>
                    <tr className={`gw-row${open === row.entity ? ' gw-row-open' : ''}`} onClick={() => toggle(row.entity)}>
                      <td><button type="button" className="gw-chevron" aria-expanded={open === row.entity} aria-label={`Show transformers at ${row.entity}`}>⌄</button></td>
                      <td className="gw-name">{row.entity}</td>
                      <td><i className={`gw-rtu ${row.status === 1 ? 'gw-rtu-ok' : 'gw-rtu-bad'}`} title={row.status === 1 ? 'Good' : 'Suspect'} /></td>
                      <td>{mw(row.actual_mw)}</td>
                      <td>{mw(row.mvar)}</td>
                      <td>{mw(row.voltage_kv)}</td>
                    </tr>
                    {open === row.entity && (
                      <tr className="gw-detail"><td /><td colSpan={5}>
                        {items === 'loading' && <span className="gw-muted">Fetching live transformer loading…</span>}
                        {items === 'error' && <span className="gw-bad">SLDC didn't return transformer data for {row.entity}.</span>}
                        {Array.isArray(items) && (items.length === 0 ? <span className="gw-muted">No transformer breakdown published for this substation.</span> : (
                          <table className="gw-subtable"><thead><tr><th>Transformer / ICT</th><th>MW</th><th>MVAR</th><th>kV</th></tr></thead>
                            <tbody>{items.map((t) => <tr key={t.transformer}><td>{t.transformer}</td><td>{mw(t.mw)}</td><td>{mw(t.mvar)}</td><td>{mw(t.voltage_kv)}</td></tr>)}</tbody></table>
                        ))}
                      </td></tr>
                    )}
                  </Fragment>
                );
              })}
              {!rows.length && <tr><td colSpan={6} className="gw-muted">No substation matches “{query}”.</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="gw-footnote">* RTU status: green = good telemetry, red = suspect. Click a substation to see its transformers.</p>
      </div>
    </section>
  );
}

const CURVE_ENTITIES: { name: string; color: string }[] = [
  { name: 'Delhi', color: 'var(--gw-yellow)' }, { name: 'BRPL', color: 'var(--gw-teal)' }, { name: 'BYPL', color: 'var(--gw-orange)' },
  { name: 'TPDDL', color: 'var(--gw-sky)' }, { name: 'MES', color: 'var(--gw-pink)' }, { name: 'NDMC', color: 'var(--gw-white)' },
];

export function DailyLoadCurve() {
  const api = useApi();
  const [day, setDay] = useState(todayIst());
  const [entity, setEntity] = useState('All');
  const [curve, setCurve] = useState<Curve | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = () => api<Curve>(`/api/v1/grid/load-curve?day=${day}`).then((c) => { if (!cancelled) { setCurve(c); setError(''); } }).catch((e) => !cancelled && setError(e instanceof Error ? e.message : 'Unable to load the load curve.'));
    void load();
    const id = day === todayIst() ? window.setInterval(load, 5 * 60_000) : undefined;
    return () => { cancelled = true; if (id) window.clearInterval(id); };
  }, [api, day]);

  const series: LineSeries[] = useMemo(() => !curve ? [] : CURVE_ENTITIES.filter((e) => entity === 'All' || e.name === entity).map((e) => ({ ...e, values: curve.series[e.name] ?? [] })), [curve, entity]);
  const delhi = curve?.series.Delhi?.filter((v): v is number => v != null) ?? [];

  return (
    <section className="gw-section">
      <SectionTitle title="Daily load curve" note="Demand across the day (MW) by entity">
        <input className="gw-date" type="date" min="2018-01-01" max={todayIst()} value={day} onChange={(e) => e.target.value && setDay(e.target.value)} aria-label="Load curve date" />
      </SectionTitle>
      <div className="gw-chips" role="tablist">
        {['All', ...CURVE_ENTITIES.map((e) => e.name)].map((name) => <button key={name} type="button" role="tab" aria-selected={entity === name} className={`gw-chip${entity === name ? ' gw-chip-active' : ''}`} onClick={() => setEntity(name)}>{name}</button>)}
      </div>
      <div className="gw-card gw-chart-card">
        {error && <div className="gw-empty gw-bad">{error}</div>}
        {!error && !curve && <div className="gw-empty">Loading load curve…</div>}
        {curve && <>
          {delhi.length > 0 && <div className="gw-curve-stats"><span>Delhi peak <b>{mw(Math.max(...delhi))} MW</b></span><span>Minimum <b>{mw(Math.min(...delhi))} MW</b></span><span>Average <b>{mw(delhi.reduce((a, b) => a + b, 0) / delhi.length)} MW</b></span><span>{curve.times.length} of 288 slots</span></div>}
          <MultiLineChart times={curve.times} series={series} />
        </>}
      </div>
    </section>
  );
}

export function PeakVsWeather({ days = 30 }: { days?: number }) {
  const api = useApi();
  const [range, setRange] = useState(days);
  const [rows, setRows] = useState<PeakWeatherDay[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    api<PeakWeatherDay[]>(`/api/v1/grid/peak-weather?days=${range}`).then((r) => { setRows(r); setError(''); }).catch((e) => setError(e instanceof Error ? e.message : 'Unable to load peaks.'));
  }, [api, range]);
  return (
    <section className="gw-section">
      <SectionTitle title={`${range}-day peak demand vs weather — Delhi`} note="Daily peak demand (MW) with temperature & humidity">
        <div className="gw-chips gw-chips-inline">{[30, 90, 365].map((n) => <button key={n} type="button" className={`gw-chip${range === n ? ' gw-chip-active' : ''}`} onClick={() => setRange(n)}>{n}d</button>)}</div>
      </SectionTitle>
      <p className="gw-source">Load: Delhi SLDC SCADA · Weather: NASA POWER &amp; Open-Meteo</p>
      <div className="gw-card gw-chart-card">
        {error && <div className="gw-empty gw-bad">{error}</div>}
        {!error && !rows && <div className="gw-empty">Loading daily peaks…</div>}
        {rows && <PeakWeatherChart days={rows} />}
      </div>
    </section>
  );
}

export function GenerationPanel({ data }: { data: LiveState }) {
  const delhiTotal = data.delhi_generation.reduce((s, r) => s + (r.actual_mw ?? 0), 0);
  const centralTotal = data.central_generation.reduce((s, r) => s + (r.actual_mw ?? 0), 0);
  return (
    <section className="gw-section">
      <SectionTitle title="Generation" stamp={asOf(data.readings_captured_at)} note="Scheduled vs actual output, bright bar = actual" />
      <div className="gw-two">
        <div className="gw-card gw-pad">
          <div className="gw-card-head"><p>Delhi generating stations</p><span className="gw-tag">{mw(delhiTotal)} MW</span></div>
          <ScheduleActualBars rows={data.delhi_generation} />
        </div>
        <div className="gw-card gw-pad">
          <div className="gw-card-head"><p>Central sector allocation (top 12)</p><span className="gw-tag">{mw(centralTotal)} MW</span></div>
          <ScheduleActualBars rows={data.central_generation} />
        </div>
      </div>
    </section>
  );
}

export function RegionalExchange({ data }: { data: LiveState }) {
  const flows = [...data.imports, ...data.exports.map((row) => ({ ...row, entity: `${row.entity} (export)` }))]
    .filter((row) => row.actual_mw)
    .sort((a, b) => Math.abs(b.actual_mw ?? 0) - Math.abs(a.actual_mw ?? 0))
    .slice(0, 12);
  return (
    <section className="gw-section">
      <SectionTitle title="Northern region & interstate flows" stamp={asOf(data.readings_captured_at)} note="Neighbouring states' drawl and Delhi's biggest import/export lines" />
      <div className="gw-two">
        <div className="gw-card gw-table-card">
          <table className="gw-table">
            <thead><tr><th>State</th><th>Schedule</th><th>Drawl</th><th>OD/UD</th><th>Load</th></tr></thead>
            <tbody>{data.states.map((row) => <tr key={row.entity}><td className="gw-name">{row.entity}</td><td>{mw(row.schedule_mw)}</td><td>{mw(row.actual_mw)}</td><td className={(row.deviation_mw ?? 0) > 0 ? 'gw-bad' : 'gw-ok'}>{signed(row.deviation_mw)}</td><td>{mw(row.load_mw)}</td></tr>)}</tbody>
          </table>
        </div>
        <div className="gw-card gw-table-card">
          <table className="gw-table">
            <thead><tr><th>Transformer / feeder</th><th>MW</th><th>MVAR</th></tr></thead>
            <tbody>{flows.map((row) => <tr key={row.entity}><td className="gw-name">{row.entity}</td><td>{mw(row.actual_mw)}</td><td>{mw(row.mvar)}</td></tr>)}</tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

/* ---- compositions --------------------------------------------------------------------------------- */

function LiveStatus({ error, data }: { error: string; data: LiveState | null }) {
  if (error && !data) return <div className="gw-card gw-empty gw-bad" role="alert">{error}</div>;
  if (!data) return <div className="gw-card gw-empty" role="status">Connecting to Delhi SLDC SCADA…</div>;
  if (!data.snapshot) return <div className="gw-card gw-empty">No live capture yet. The first one is being fetched from SLDC.</div>;
  return null;
}

/** Frequency, demand snapshot and DISCOM drawl: the top of the live board, also used on the dashboard. */
export function LiveGridSummary() {
  const { data, error } = useLiveGrid();
  return (
    <div className="gw-board">
      <LiveStatus error={error} data={data} />
      {data?.snapshot && <>
        <div className="gw-hero"><FrequencyCard data={data} /><DemandSnapshot data={data} /></div>
        <DiscomDrawl data={data} />
      </>}
    </div>
  );
}

export function LiveGridBoard() {
  const { data, error } = useLiveGrid();
  return (
    <div className="gw-board">
      <header className="gw-banner">
        <div className="gw-brand"><span className="gw-logo">DG</span><div><h1>Delhi Grid Watch</h1><p>Real-time view of Delhi&apos;s transmission &amp; discom grid · powered by DTL SCADA via GridSense</p></div></div>
        <LiveClock />
      </header>
      <LiveStatus error={error} data={data} />
      {data?.snapshot && <>
        <div className="gw-hero"><FrequencyCard data={data} /><DemandSnapshot data={data} /></div>
        <DiscomDrawl data={data} />
        <TransmissionStatus data={data} />
      </>}
      <DailyLoadCurve />
      <PeakVsWeather />
      {data?.snapshot && <>
        <GenerationPanel data={data} />
        <RegionalExchange data={data} />
      </>}
    </div>
  );
}
