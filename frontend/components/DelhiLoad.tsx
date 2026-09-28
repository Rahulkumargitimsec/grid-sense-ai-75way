'use client';

import { useEffect, useMemo, useState } from 'react';
import { useApi } from './api';

type Driver = { factor: string; effect_mw: number };
type Day = { date: string; tag: string; festival_name: string | null; weekday: string; actual_avg_mw: number; vs_year_avg_mw: number; peak_mw: number; rain_mm: number; temp_c: number; flags: string[]; drivers: Driver[]; explanation: string };
type Scenario = { zone: string; day_type: string; weather: string; time: string; predicted_mw: number; peak_probability: number };
type Summary = {
  peak_threshold_mw: number;
  data_quality: { rows: number; floor_share: number; jan_night_temp_max: number; public_holiday_days: number; avg_load_mw: number };
  model: { test_period: string; mae_mw: number; r2: number; baseline_mae_mw: number; peak_auc: number };
  effects: { events: { event: string; raw_uplift_mw: number; controlled_uplift_mw: number }[]; weather_and_zone: { factor: string; effect_mw: number }[] };
  zone_outlook: { zones: { zone: string; avg_load_mw: number; peak_hour_share: number }[]; area_mix: { start: Record<string, number | string>; end: Record<string, number | string>; mix_weighted_load_start_mw: number; mix_weighted_load_end_mw: number } };
};

const mw = (value: number) => `${Math.round(value).toLocaleString('en-IN')} MW`;
const signedMw = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${mw(Math.abs(value))}`;
const pct = (value: number) => `${Math.round(value * 100)}%`;
const DAY_TYPE = 'Day type (festival / holiday / weekend)';

function errorText(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

export function DriverBars({ rows }: { rows: { label: string; value: number; note?: string }[] }) {
  const max = Math.max(1, ...rows.map((row) => Math.abs(row.value)));
  return (
    <div className="delhi-bars" role="list">
      {rows.map((row) => (
        <div className="delhi-bar-row" role="listitem" key={row.label} title={row.note}>
          <span className="delhi-bar-label">{row.label}</span>
          <span className="delhi-bar-track">
            <span className={`delhi-bar ${row.value >= 0 ? 'delhi-bar-up' : 'delhi-bar-down'}`} style={{ width: `${(Math.abs(row.value) / max) * 50}%` }} />
          </span>
          <span className={`delhi-bar-value ${row.value >= 0 ? 'delhi-up' : 'delhi-down'}`}>{signedMw(row.value)}</span>
        </div>
      ))}
    </div>
  );
}

function SyntheticNotice() {
  return <p className="delhi-notice"><strong>Synthetic dataset.</strong> These results come from the Kaggle "Delhi Power Load" dataset (2023, hourly), which is simulated rather than metered data. Use them to explore the method, and retrain on real SLDC load before planning capacity.</p>;
}

export function DelhiDayExplainer() {
  const api = useApi();
  const [days, setDays] = useState<Day[]>([]);
  const [date, setDate] = useState('2023-11-12');
  const [error, setError] = useState('');

  useEffect(() => {
    api<Day[]>('/api/v1/delhi-load/days').then((items) => { setDays(items); setError(''); }).catch((requestError) => setError(errorText(requestError, 'Unable to load the Delhi day explanations.')));
  }, [api]);

  const selected = days.find((day) => day.date === date);
  const groups = useMemo(() => [
    ['Festivals', days.filter((day) => day.festival_name)],
    ['Highest-load days', days.filter((day) => day.tag.startsWith('Highest'))],
    ['Lowest-load days', days.filter((day) => day.tag.startsWith('Lowest'))],
  ] as const, [days]);

  return (
    <div className="delhi-day">
      <div className="panel-heading"><div><h2>Why was a day high or low?</h2><p>Pick any day in 2023. Each factor is reset to a typical value and the model measures how far that day's average load moves.</p></div></div>
      <div className="workspace-toolbar">
        <label>Notable days<select value={groups.some(([, items]) => items.some((day) => day.date === date)) ? date : ''} onChange={(event) => event.target.value && setDate(event.target.value)}>
          <option value="">Choose a day…</option>
          {groups.map(([label, items]) => <optgroup label={label} key={label}>{items.map((day) => <option value={day.date} key={day.date}>{day.festival_name ?? `${day.weekday.slice(0, 3)} ${day.date}`} ({signedMw(day.vs_year_avg_mw)})</option>)}</optgroup>)}
        </select></label>
        <label>Any date<input type="date" min="2023-01-01" max="2023-12-31" value={date} onChange={(event) => event.target.value && setDate(event.target.value)} /></label>
      </div>
      {error && <div className="workspace-state workspace-state-error" role="alert">{error}</div>}
      {!error && days.length === 0 && <div className="workspace-state">Loading 365 days of explanations…</div>}
      {selected && (
        <div className="delhi-day-detail">
          <div className="delhi-day-top">
            <div>
              <p className="eyebrow">{selected.tag || (selected.flags.length ? selected.flags.join(' · ') : 'Working day')}</p>
              <h3>{new Date(`${selected.date}T00:00:00`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}</h3>
            </div>
            <strong className={selected.vs_year_avg_mw >= 0 ? 'delhi-up' : 'delhi-down'}>{signedMw(selected.vs_year_avg_mw)} <small>vs 2023 average</small></strong>
          </div>
          <p className="delhi-day-why">{selected.explanation}</p>
          <div className="delhi-facts">
            <span>Avg load<strong>{mw(selected.actual_avg_mw)}</strong></span>
            <span>Peak hour<strong>{mw(selected.peak_mw)}</strong></span>
            <span>Rain<strong>{selected.rain_mm} mm/h</strong></span>
            <span>Temperature<strong>{selected.temp_c}°C</strong></span>
          </div>
          {selected.drivers.length > 0
            ? <DriverBars rows={selected.drivers.map((driver) => ({ label: driver.factor === DAY_TYPE ? 'Day type' : driver.factor, value: driver.effect_mw, note: driver.factor === DAY_TYPE ? 'Festival, public holiday and weekend combined' : undefined }))} />
            : <p className="workspace-muted">No factor moved this day's load by more than 40 MW.</p>}
        </div>
      )}
      {!selected && days.length > 0 && <div className="workspace-state">No data for that date. Choose a date in 2023.</div>}
    </div>
  );
}

const DAY_TYPES = ['Normal weekday', 'Weekend', 'Public holiday', 'Festival (e.g. Diwali)'];
const WEATHER = ['Hot & dry', 'Mild & rainy'];
const ZONES = ['High', 'Medium', 'Low'];

function riskLabel(probability: number) {
  if (probability >= 0.5) return <span className="delhi-risk delhi-risk-high">High peak risk · {pct(probability)}</span>;
  if (probability >= 0.1) return <span className="delhi-risk delhi-risk-medium">Watch · {pct(probability)}</span>;
  return <span className="delhi-risk delhi-risk-low">Low · {pct(probability)}</span>;
}

export function DelhiLoadInsights() {
  const api = useApi();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [zone, setZone] = useState('High');
  const [dayType, setDayType] = useState('Festival (e.g. Diwali)');
  const [weather, setWeather] = useState('Hot & dry');
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([api<Summary>('/api/v1/delhi-load/summary'), api<Scenario[]>('/api/v1/delhi-load/scenarios')])
      .then(([summaryData, scenarioData]) => { setSummary(summaryData); setScenarios(scenarioData); setError(''); })
      .catch((requestError) => setError(errorText(requestError, 'Unable to load the Delhi load study.')));
  }, [api]);

  if (error) return <div className="workspace-state workspace-state-error" role="alert">{error}</div>;
  if (!summary) return <div className="workspace-state">Loading the Delhi load study…</div>;

  const { model, effects, zone_outlook: outlook } = summary;
  const mix = outlook.area_mix;
  const picked = scenarios.filter((row) => row.zone === zone && row.day_type === dayType && row.weather === weather);
  const growth = mix.mix_weighted_load_end_mw / mix.mix_weighted_load_start_mw - 1;

  return (
    <div className="delhi-insights">
      <SyntheticNotice />
      <div className="metric-grid">
        <article className="metric-card metric-card-blue"><p>Average load</p><strong>{mw(summary.data_quality.avg_load_mw)}</strong><span>{summary.data_quality.rows.toLocaleString('en-IN')} hourly readings, 2023</span></article>
        <article className="metric-card metric-card-green"><p>Model error</p><strong>±{mw(model.mae_mw)}</strong><span>Nov–Dec holdout · hour-of-day average ±{mw(model.baseline_mae_mw)}</span></article>
        <article className="metric-card metric-card-amber"><p>Peak-hour detection</p><strong>{model.peak_auc.toFixed(3)} AUC</strong><span>Hours ≥ {mw(summary.peak_threshold_mw)}</span></article>
        <article className="metric-card metric-card-slate"><p>High-development share</p><strong>{mix.start.high}% → {mix.end.high}%</strong><span>Lifts citywide demand {pct(growth)} at each zone's average load</span></article>
      </div>

      <div className="operations-grid">
        <section className="delhi-block">
          <h3>What moves hourly load, all else equal</h3>
          <DriverBars rows={[...effects.weather_and_zone.map((row) => ({ label: row.factor, value: row.effect_mw })), ...effects.events.map((row) => ({ label: `${row.event} vs normal day`, value: row.controlled_uplift_mw }))].sort((a, b) => b.value - a.value)} />
          <p className="workspace-muted">Load also roughly doubles in the 15:00 and 23:00 hours every day. That spike is built into this synthetic dataset.</p>
        </section>
        <section className="delhi-block">
          <h3>Where more power is needed</h3>
          <div className="delhi-table-wrap"><table className="delhi-table">
            <thead><tr><th>Zone</th><th>Avg load</th><th>Hours ≥ {(summary.peak_threshold_mw / 1000).toFixed(0)} GW</th><th>Area share</th></tr></thead>
            <tbody>{[...outlook.zones].reverse().map((row) => {
              const key = row.zone === 'Medium' ? 'medium' : row.zone.toLowerCase();
              return <tr key={row.zone}><td>{row.zone} development</td><td>{mw(row.avg_load_mw)}</td><td>{pct(row.peak_hour_share)}</td><td>{mix.start[key]}% → {mix.end[key]}%</td></tr>;
            })}</tbody>
          </table></div>
          <h3>Festivals &amp; holidays</h3>
          <div className="delhi-table-wrap"><table className="delhi-table">
            <thead><tr><th>Day type</th><th>Raw difference</th><th>All else equal</th></tr></thead>
            <tbody>{effects.events.map((row) => <tr key={row.event}><td>{row.event}</td><td>{signedMw(row.raw_uplift_mw)}</td><td><strong>{signedMw(row.controlled_uplift_mw)}</strong></td></tr>)}</tbody>
          </table></div>
          <p className="workspace-muted">These uplifts don't stack. A festival on a weekend, like Diwali on 12 Nov 2023, adds about the same as either one alone.</p>
        </section>
      </div>

      <section className="delhi-block">
        <h3>What-if: peak risk by zone, day and weather</h3>
        <div className="workspace-toolbar">
          <label>Zone<select value={zone} onChange={(event) => setZone(event.target.value)}>{ZONES.map((value) => <option key={value} value={value}>{value} development</option>)}</select></label>
          <label>Day<select value={dayType} onChange={(event) => setDayType(event.target.value)}>{DAY_TYPES.map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>Weather<select value={weather} onChange={(event) => setWeather(event.target.value)}>{WEATHER.map((value) => <option key={value}>{value}</option>)}</select></label>
        </div>
        <div className="delhi-scenarios">
          {picked.map((row) => <article className="delhi-scenario" key={row.time}><span>{row.time}</span><strong>{mw(row.predicted_mw)}</strong>{riskLabel(row.peak_probability)}</article>)}
        </div>
        <p className="workspace-muted">Modelled for a June day with the end-of-2023 area mix. Hot &amp; dry means 42°C with no rain. Mild &amp; rainy means 26°C with 14 mm/h of rain.</p>
      </section>
    </div>
  );
}
