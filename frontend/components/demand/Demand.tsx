'use client';

import { useEffect, useMemo, useState } from 'react';
import { useApi } from '../api';
import { useAuth } from '../AuthProvider';
import { DriverBars } from '../DelhiLoad';
import { BandPoint, ForecastBandChart, MultiLineChart, XYLineChart } from '../grid/GridCharts';

/* ---- API shapes (backend/app/services/demand_model.py) ------------------------------------------- */

type Driver = { group: string; label: string; effect_mw: number; detail: string };
type Weather = { temperature_c: number | null; humidity_pct: number | null; rain_24h_mm: number | null; wind_speed_ms: number | null };
type Window = { from: string; to: string };
type Thresholds = { high_mw: number; critical_mw: number; low_mw: number; high_percentile: number; critical_percentile: number; low_percentile: number };
type Metrics = Record<string, number | string>;
type ForecastDay = {
  date: string; weekday: string; holiday: string | null; hours_covered: number;
  peak_mw: number; peak_at: string; probability_high: number; risk: 'critical' | 'high' | 'watch' | 'normal'; high_windows: Window[];
  normal_at_peak_mw: number; drivers: Driver[]; weather_at_peak: Weather; reason: string;
  min_mw: number; min_at: string; probability_low: number; low_level: 'low' | 'watch' | 'normal'; low_windows: Window[];
  normal_at_min_mw: number; min_drivers: Driver[]; weather_at_min: Weather; min_reason: string;
};
type Forecast = { generated_at: string; trained_at: string; thresholds: Thresholds; metrics: Metrics; hours: (BandPoint & { humidity_pct: number | null; rain_mm: number | null })[]; days: ForecastDay[] };
type Explained = {
  date: string; weekday: string; holiday: string | null; has_actual: boolean; thresholds: Thresholds;
  peak_mw: number; peak_at: string; level: string; percentile_in_past_year: number | null; normal_at_peak_mw: number; model_at_peak_mw: number; unexplained_at_peak_mw: number | null;
  drivers: Driver[]; weather_at_peak: Weather; reason: string;
  min_mw: number; min_at: string; low_level: string; normal_at_min_mw: number; model_at_min_mw: number; unexplained_at_min_mw: number | null;
  min_drivers: Driver[]; weather_at_min: Weather; min_reason: string;
  hours: { at: string; actual_mw: number | null; predicted_mw: number; normal_mw: number }[];
};
type Ranked = { date: string; weekday: string; holiday: string | null; load_mw: number; at: string; normal_mw: number; top_reasons: Driver[]; weather: Weather };
type Curve = { x: number; load_mw: number };
type FestivalDay = {
  date: string; weekday: string; name: string; primary_name: string; gazetted: boolean; status: 'past' | 'forecast' | 'upcoming' | 'no_data'; has_actual?: boolean;
  mean_mw?: number; peak_mw?: number; peak_at?: string; without_festival_mean_mw?: number; festival_effect_mw?: number; festival_effect_pct?: number;
  evening_effect_mw?: number | null; temperature_max_c?: number | null; typical_effect_mw: number | null; typical_effect_pct: number | null;
};
type Insights = {
  trained_at: string; thresholds: Thresholds;
  holidays: { name: string; gazetted: boolean; occurrences: number; effect_mw: number; effect_pct: number; afternoon_effect_mw: number | null; evening_effect_mw: number | null; last_seen: string; next_date: string | null }[];
  weather: { temperature: { season: string; typical_c: number; mw_per_degree: number | null; curve: Curve[] }[]; humidity: { season: string; curve: Curve[] }[]; rain: { season: string; curve: Curve[] }[] };
};
type ModelInfo = { trained_at: string; data_until: string; thresholds: Thresholds; metrics: Metrics; importance: { group: string; label: string; mae_increase_mw: number }[] };

/* ---- helpers -------------------------------------------------------------------------------------- */

const mw = (v: number | null | undefined) => (v == null ? '—' : Math.round(v).toLocaleString('en-IN'));
const hhmm = (iso: string) => iso.slice(11, 16);
const pct = (v: number) => `${Math.round(v * 100)}%`;
const shortDate = (iso: string) => new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' });
const longDate = (iso: string) => new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
const errorText = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);
const RISK_LABEL: Record<string, string> = { critical: 'Critical', high: 'High', watch: 'Watch', normal: 'Normal', low: 'Low' };

function todayIst() {
  return new Date(Date.now() + (330 + new Date().getTimezoneOffset()) * 60000).toISOString().slice(0, 10);
}

function Badge({ level }: { level: string }) {
  return <span className={`dm-badge dm-${level}`}>{RISK_LABEL[level] ?? level}</span>;
}

function windowsText(windows: Window[]) {
  return windows.map((w) => `${hhmm(w.from)}–${w.to.slice(11, 13) === '00' ? '24:00' : hhmm(w.to)}`).join(', ');
}

function Drivers({ drivers }: { drivers: Driver[]; direction: 'up' | 'down' }) {
  // Biggest effect first; bar colour already shows whether it pushed demand up or down.
  const rows = drivers.filter((d) => Math.abs(d.effect_mw) >= 5).sort((a, b) => Math.abs(b.effect_mw) - Math.abs(a.effect_mw));
  if (!rows.length) return <p className="gw-muted">Every factor was close to normal.</p>;
  return <DriverBars rows={rows.map((d) => ({ label: d.label, value: d.effect_mw, note: d.detail }))} />;
}

function WeatherFacts({ weather }: { weather: Weather }) {
  return (
    <div className="dm-facts">
      {weather.temperature_c != null && <span>Temp <b>{weather.temperature_c}°C</b></span>}
      {weather.humidity_pct != null && <span>Humidity <b>{Math.round(weather.humidity_pct)}%</b></span>}
      {weather.rain_24h_mm != null && <span>Rain 24 h <b>{weather.rain_24h_mm} mm</b></span>}
      {weather.wind_speed_ms != null && <span>Wind <b>{weather.wind_speed_ms} m/s</b></span>}
    </div>
  );
}

function Loading({ error, what }: { error: string; what: string }) {
  if (error) return <div className="gw-card gw-empty gw-bad" role="alert">{error}</div>;
  return <div className="gw-card gw-empty" role="status">Loading {what}…</div>;
}

function Section({ title, note, children, side }: { title: string; note?: string; children: React.ReactNode; side?: React.ReactNode }) {
  return (
    <section className="gw-section">
      <div className="gw-section-head"><div><h2>{title}</h2>{note && <p className="gw-stamp">{note}</p>}</div>{side && <div className="gw-section-side">{side}</div>}</div>
      {children}
    </section>
  );
}

/* ---- 7-day outlook: when will demand be high or low ------------------------------------------------ */

function useForecast() {
  const api = useApi();
  const [data, setData] = useState<Forecast | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const load = () => api<Forecast>('/api/v1/demand/forecast').then((d) => { setData(d); setError(''); }).catch((e) => setError(errorText(e, 'The demand forecast is unavailable.')));
    void load();
    const id = window.setInterval(load, 10 * 60_000);
    return () => window.clearInterval(id);
  }, [api]);
  return { data, error };
}

function DayCards({ days, selected, onSelect }: { days: ForecastDay[]; selected?: string; onSelect?: (date: string) => void }) {
  return (
    <div className="dm-days">
      {days.map((d) => (
        <button type="button" key={d.date} className={`dm-day gw-card${selected === d.date ? ' dm-day-selected' : ''}`} onClick={() => onSelect?.(d.date)}>
          <span className="dm-day-date">{shortDate(d.date)}{d.hours_covered < 24 && ' (partial)'}</span>
          <span className="dm-day-holiday">{d.holiday ?? ''}</span>
          <Badge level={d.risk} />
          <strong>{mw(d.peak_mw)}<small>MW</small></strong>
          <span className="dm-day-sub">peak {hhmm(d.peak_at)} · {pct(d.probability_high)} high</span>
          <span className="dm-day-sub">min {mw(d.min_mw)} at {hhmm(d.min_at)}</span>
          {d.low_level !== 'normal' && <Badge level={d.low_level === 'low' ? 'low' : 'watch'} />}
        </button>
      ))}
    </div>
  );
}

/** Compact 7-day strip for the dashboard. */
export function DemandOutlookStrip() {
  const { data, error } = useForecast();
  return (
    <Section title="Next 7 days: when demand will be high" note={data ? `High mark ${mw(data.thresholds.high_mw)} MW · low mark ${mw(data.thresholds.low_mw)} MW` : undefined}>
      {!data ? <Loading error={error} what="the demand outlook" /> : <DayCards days={data.days} />}
    </Section>
  );
}

export function DemandOutlook() {
  const { data, error } = useForecast();
  const [selected, setSelected] = useState<string>();
  const day = data?.days.find((d) => d.date === selected) ?? data?.days.find((d) => d.risk !== 'normal') ?? data?.days[0];

  if (!data) return <Loading error={error} what="the 7-day demand forecast" />;
  const m = data.metrics;
  return (
    <div className="gw-board">
      <Section title="When will Delhi's demand be high or low?" note={`Forecast updated ${data.generated_at.slice(11, 16)} IST · model trained ${data.trained_at.slice(0, 10)}`}>
        <DayCards days={data.days} selected={day?.date} onSelect={setSelected} />
      </Section>
      <div className="gw-card gw-chart-card">
        <ForecastBandChart points={data.hours} highMw={data.thresholds.high_mw} lowMw={data.thresholds.low_mw} selectedDate={day?.date} onSelectDate={setSelected} />
      </div>
      {day && (
        <Section title={`Why: ${longDate(day.date)}`} note={day.holiday ? day.holiday : undefined}>
          <div className="dm-detail">
            <article className="gw-card dm-reason-card">
              <div className="dm-reason-head"><h3>Peak demand</h3><Badge level={day.risk} /></div>
              <div className="dm-big">{mw(day.peak_mw)}<small>MW at {hhmm(day.peak_at)}</small></div>
              <p className="dm-reason">{day.reason}</p>
              <div className="dm-facts"><span>Normal for this hour <b>{mw(day.normal_at_peak_mw)} MW</b></span><span>Chance of high <b>{pct(day.probability_high)}</b></span></div>
              {day.high_windows.length > 0 && <p className="dm-windows">High between {windowsText(day.high_windows)}</p>}
              <WeatherFacts weather={day.weather_at_peak} />
              <Drivers drivers={day.drivers} direction="up" />
            </article>
            <article className="gw-card dm-reason-card">
              <div className="dm-reason-head"><h3>Minimum demand</h3><Badge level={day.low_level === 'normal' ? 'normal' : day.low_level === 'low' ? 'low' : 'watch'} /></div>
              <div className="dm-big">{mw(day.min_mw)}<small>MW at {hhmm(day.min_at)}</small></div>
              <p className="dm-reason">{day.min_reason}</p>
              <div className="dm-facts"><span>Normal for this hour <b>{mw(day.normal_at_min_mw)} MW</b></span><span>Chance of low <b>{pct(day.probability_low)}</b></span></div>
              {day.low_windows.length > 0 && <p className="dm-windows">Low between {windowsText(day.low_windows)}</p>}
              <WeatherFacts weather={day.weather_at_min} />
              <Drivers drivers={day.min_drivers} direction="down" />
            </article>
          </div>
        </Section>
      )}
      <Section title="How reliable is this?" note={`Tested on ${m.test_period}, a full year the model never saw`}>
        <div className="dm-metrics">
          <div className="gw-card dm-metric"><p>Hourly error</p><strong>±{mw(Number(m.mae_mw))} MW</strong><span>{m.mape_pct}% average · simple recent-average guess ±{mw(Number(m.baseline_recent_profile_mae_mw))} MW</span></div>
          <div className="gw-card dm-metric"><p>High days caught</p><strong>{m.high_days_flagged_watch_or_above} / {m.high_days_in_test}</strong><span>flagged Watch or higher up to a week ahead</span></div>
          <div className="gw-card dm-metric"><p>High hours</p><strong>{pct(Number(m.high_hour_precision))} right</strong><span>{pct(Number(m.high_hour_recall))} of high hours caught</span></div>
          <div className="gw-card dm-metric"><p>Peak & minimum</p><strong>±{mw(Number(m.daily_peak_mae_mw))} MW</strong><span>peak hour within 1 h on {m.peak_hour_within_1h_pct}% of days · minimum ±{mw(Number(m.daily_min_mae_mw))} MW</span></div>
        </div>
        <p className="gw-footnote">&quot;High&quot; = above {mw(data.thresholds.high_mw)} MW (top {100 - data.thresholds.high_percentile}% of hours in the past year), &quot;critical&quot; above {mw(data.thresholds.critical_mw)} MW, &quot;low&quot; below {mw(data.thresholds.low_mw)} MW. Future days use the Open-Meteo weather forecast, so they carry that forecast&apos;s error too.</p>
      </Section>
    </div>
  );
}

/* ---- explain any day: why was demand high (or low) ------------------------------------------------- */

export function DemandExplainer() {
  const api = useApi();
  const [day, setDay] = useState(() => todayIst());
  const [data, setData] = useState<Explained | null>(null);
  const [error, setError] = useState('');
  const [high, setHigh] = useState<Ranked[]>([]);
  const [low, setLow] = useState<Ranked[]>([]);

  useEffect(() => {
    setData(null);
    api<Explained>(`/api/v1/demand/explain?day=${day}`).then((d) => { setData(d); setError(''); }).catch((e) => setError(errorText(e, 'Unable to explain that day.')));
  }, [api, day]);
  useEffect(() => {
    api<Ranked[]>('/api/v1/demand/ranked-days?kind=high&limit=10').then(setHigh).catch(() => setHigh([]));
    api<Ranked[]>('/api/v1/demand/ranked-days?kind=low&limit=10').then(setLow).catch(() => setLow([]));
  }, [api]);

  const curve = useMemo(() => !data ? null : {
    times: data.hours.map((h) => hhmm(h.at)),
    series: [
      ...(data.has_actual ? [{ name: 'Actual', color: 'var(--gw-white)', values: data.hours.map((h) => h.actual_mw) }] : []),
      { name: 'Model', color: 'var(--gw-yellow)', values: data.hours.map((h) => h.predicted_mw) },
      { name: 'Normal day', color: 'var(--gw-sky)', values: data.hours.map((h) => h.normal_mw) },
    ],
  }, [data]);

  const maxDate = new Date(Date.now() + 6 * 86400000).toISOString().slice(0, 10);
  const pick = (date: string) => { setDay(date); window.scrollTo({ top: 0, behavior: 'smooth' }); };

  return (
    <div className="gw-board">
      <Section title="Why was demand high or low?" note="Real Delhi SLDC load, 2018 onward. Future dates use the weather forecast." side={<input className="gw-date" type="date" min="2018-02-01" max={maxDate} value={day} onChange={(e) => e.target.value && setDay(e.target.value)} aria-label="Day to explain" />}>
        {!data ? <Loading error={error} what="the explanation" /> : (
          <>
            <div className="dm-detail">
              <article className="gw-card dm-reason-card">
                <div className="dm-reason-head"><h3>Peak · {longDate(data.date)}</h3><Badge level={data.level} /></div>
                <div className="dm-big">{mw(data.peak_mw)}<small>MW at {hhmm(data.peak_at)}</small></div>
                <p className="dm-reason">{data.reason}</p>
                <div className="dm-facts">
                  <span>Normal <b>{mw(data.normal_at_peak_mw)} MW</b></span>
                  {data.percentile_in_past_year != null && <span>Higher than <b>{data.percentile_in_past_year}%</b> of days in the year before</span>}
                  {data.unexplained_at_peak_mw != null && <span>Not explained by the model <b>{data.unexplained_at_peak_mw > 0 ? '+' : ''}{mw(data.unexplained_at_peak_mw)} MW</b></span>}
                </div>
                <WeatherFacts weather={data.weather_at_peak} />
                <Drivers drivers={data.drivers} direction="up" />
              </article>
              <article className="gw-card dm-reason-card">
                <div className="dm-reason-head"><h3>Minimum</h3><Badge level={data.low_level} /></div>
                <div className="dm-big">{mw(data.min_mw)}<small>MW at {hhmm(data.min_at)}</small></div>
                <p className="dm-reason">{data.min_reason}</p>
                <div className="dm-facts">
                  <span>Normal <b>{mw(data.normal_at_min_mw)} MW</b></span>
                  {data.unexplained_at_min_mw != null && <span>Not explained <b>{data.unexplained_at_min_mw > 0 ? '+' : ''}{mw(data.unexplained_at_min_mw)} MW</b></span>}
                </div>
                <WeatherFacts weather={data.weather_at_min} />
                <Drivers drivers={data.min_drivers} direction="down" />
              </article>
            </div>
            {curve && <div className="gw-card gw-chart-card"><MultiLineChart times={curve.times} series={curve.series} height={300} /></div>}
            <p className="gw-footnote">&quot;Normal&quot; means the same hour with typical weather for that month, an ordinary weekday with no holiday, and last year&apos;s demand level. The reasons add up to the gap between the model and normal.</p>
          </>
        )}
      </Section>
      <FestivalExplorer onPick={pick} />
      <div className="gw-two">
        <Section title="Highest-demand days, past year">
          <div className="dm-ranked">{high.map((r) => <button type="button" className="dm-ranked-row" key={r.date} onClick={() => pick(r.date)}><b>{shortDate(r.date)}</b><b>{mw(r.load_mw)} MW</b><span>{r.top_reasons.map((d) => `${d.label} +${mw(d.effect_mw)}`).join(' · ') || '—'}</span></button>)}</div>
        </Section>
        <Section title="Lowest-demand days, past year">
          <div className="dm-ranked">{low.map((r) => <button type="button" className="dm-ranked-row" key={r.date} onClick={() => pick(r.date)}><b>{shortDate(r.date)}</b><b>{mw(r.load_mw)} MW</b><span>{r.holiday ? `${r.holiday} · ` : ''}{r.top_reasons.map((d) => `${d.label} ${mw(d.effect_mw)}`).join(' · ') || '—'}</span></button>)}</div>
        </Section>
      </div>
    </div>
  );
}

/* ---- festivals by year: real effect on demand ------------------------------------------------------ */

function signedMw(value: number | null | undefined) {
  if (value == null) return '—';
  return `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(Math.round(value)).toLocaleString('en-IN')} MW`;
}

export function FestivalExplorer({ onPick }: { onPick: (date: string) => void }) {
  const api = useApi();
  const thisYear = Number(todayIst().slice(0, 4));
  const [year, setYear] = useState(thisYear);
  const [scope, setScope] = useState<'gazetted' | 'all'>('gazetted');
  const [rows, setRows] = useState<FestivalDay[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setRows(null);
    api<FestivalDay[]>(`/api/v1/demand/festivals?year=${year}`).then((items) => { setRows(items); setError(''); }).catch((e) => setError(errorText(e, 'Unable to load festivals.')));
  }, [api, year]);

  const years = Array.from({ length: thisYear + 2 - 2018 }, (_, i) => 2018 + i);
  const shown = (rows ?? []).filter((row) => scope === 'all' || row.gazetted);
  const measured = shown.filter((row) => row.festival_effect_mw != null);
  const biggest = [...measured].sort((a, b) => (a.festival_effect_mw ?? 0) - (b.festival_effect_mw ?? 0))[0];
  const next = shown.find((row) => row.status !== 'past');

  return (
    <Section title="Festivals & holidays: what they did to demand" note="Real Delhi SLDC load for past years; forecast for the coming week; past-years average further ahead"
      side={<div className="gw-chips gw-chips-inline"><button type="button" className={`gw-chip${scope === 'gazetted' ? ' gw-chip-active' : ''}`} onClick={() => setScope('gazetted')}>Gazetted</button><button type="button" className={`gw-chip${scope === 'all' ? ' gw-chip-active' : ''}`} onClick={() => setScope('all')}>All incl. restricted</button></div>}>
      <div className="gw-chips" role="group" aria-label="Year">
        {years.map((value) => <button key={value} type="button" className={`gw-chip${year === value ? ' gw-chip-active' : ''}`} onClick={() => setYear(value)}>{value}</button>)}
      </div>
      {!rows ? <Loading error={error} what={`${year} festivals`} /> : (
        <>
          <div className="dm-metrics">
            <div className="gw-card dm-metric"><p>{year} festivals shown</p><strong>{shown.length}</strong><span>{measured.length} with measured or forecast load</span></div>
            <div className="gw-card dm-metric"><p>Biggest drop</p><strong>{biggest ? signedMw(biggest.festival_effect_mw) : '—'}</strong><span>{biggest ? `${biggest.primary_name}, ${shortDate(biggest.date)} (${biggest.festival_effect_pct}%)` : 'no measured days yet'}</span></div>
            <div className="gw-card dm-metric"><p>Average festival effect</p><strong>{measured.length ? signedMw(measured.reduce((sum, row) => sum + (row.festival_effect_mw ?? 0), 0) / measured.length) : '—'}</strong><span>average per festival day</span></div>
            <div className="gw-card dm-metric"><p>Next</p><strong>{next ? shortDate(next.date) : '—'}</strong><span>{next ? `${next.primary_name}${next.typical_effect_mw != null ? ` · usually ${signedMw(next.typical_effect_mw)}` : ''}` : `no more festivals in ${year}`}</span></div>
          </div>
          <div className="gw-card gw-table-card">
            <div className="gw-table-scroll">
              <table className="gw-table">
                <thead><tr><th>Date</th><th>Festival</th><th>Type</th><th>Avg load</th><th>Without festival</th><th>Festival effect</th><th>Evening</th><th>Usually</th><th /></tr></thead>
                <tbody>{shown.map((row) => (
                  <tr key={row.date} className="gw-row" onClick={() => row.status !== 'upcoming' && onPick(row.date)}>
                    <td className="gw-name">{shortDate(row.date)}</td>
                    <td className="gw-name">{row.name.split(';')[0]}{row.name.includes(';') && <small className="gw-muted"> +{row.name.split(';').length - 1}</small>}</td>
                    <td>{row.gazetted ? 'Gazetted' : 'Restricted'}</td>
                    <td>{row.mean_mw != null ? mw(row.mean_mw) + ' MW' : '—'}{row.status === 'forecast' && <small className="gw-muted"> fc</small>}{row.status === 'past' && row.has_actual === false && <small className="gw-muted"> est</small>}</td>
                    <td>{row.without_festival_mean_mw != null ? mw(row.without_festival_mean_mw) + ' MW' : '—'}</td>
                    <td className={(row.festival_effect_mw ?? 0) < 0 ? 'gw-down' : 'gw-up'}>{row.festival_effect_mw != null ? `${signedMw(row.festival_effect_mw)} (${row.festival_effect_pct}%)` : '—'}</td>
                    <td>{signedMw(row.evening_effect_mw)}</td>
                    <td>{row.typical_effect_mw != null ? signedMw(row.typical_effect_mw) : '—'}</td>
                    <td>{row.status === 'upcoming' ? <span className="dm-badge dm-normal">Upcoming</span> : <span className="ops-why">Explain →</span>}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
          <p className="gw-footnote">&quot;Without festival&quot; is the model&apos;s estimate of the same day with the same weather and demand level but no holiday, so the effect is the festival alone. &quot;Usually&quot; is the average effect across 2018–2026. Click a past or forecast day for its full explanation.</p>
        </>
      )}
    </Section>
  );
}

/* ---- holidays & weather: what moves Delhi's demand ------------------------------------------------- */

const SEASON_COLORS = ['var(--gw-orange)', 'var(--gw-teal)', 'var(--gw-yellow)', 'var(--gw-sky)'];

export function DemandInsights() {
  const api = useApi();
  const { user } = useAuth();
  const [data, setData] = useState<Insights | null>(null);
  const [info, setInfo] = useState<ModelInfo | null>(null);
  const [error, setError] = useState('');
  const [training, setTraining] = useState(false);
  const [view, setView] = useState<'gazetted' | 'all'>('gazetted');

  const load = () => Promise.all([api<Insights>('/api/v1/demand/insights'), api<ModelInfo>('/api/v1/demand/model')])
    .then(([i, m]) => { setData(i); setInfo(m); setError(''); })
    .catch((e) => setError(errorText(e, 'Holiday and weather insights are unavailable.')));
  useEffect(() => { void load(); }, [api]);

  const retrain = async () => {
    setTraining(true);
    try { await api('/api/v1/demand/model/train', { method: 'POST' }); await load(); } catch (e) { setError(errorText(e, 'Training failed.')); } finally { setTraining(false); }
  };

  if (!data || !info) return <Loading error={error} what="holiday and weather effects" />;
  const holidays = data.holidays.filter((h) => view === 'all' || h.gazetted);
  const canTrain = user?.role === 'super_admin' || user?.role === 'research_analyst';

  return (
    <div className="gw-board">
      <Section title="What moves Delhi's demand" note={`Model trained ${info.trained_at.slice(0, 16).replace('T', ' ')} UTC on SLDC load up to ${info.data_until.slice(0, 10)}`} side={canTrain && <button className="secondary-button" type="button" onClick={() => void retrain()} disabled={training}>{training ? 'Training… (~30 s)' : 'Retrain now'}</button>}>
        <div className="gw-card gw-pad">
          <div className="gw-card-head"><p>How much each factor matters (error increase when it&apos;s scrambled)</p></div>
          <DriverBars rows={info.importance.map((row) => ({ label: row.label, value: row.mae_increase_mw }))} />
        </div>
      </Section>

      <Section title="Holidays & festivals" note="Average change in demand on the day itself, everything else as it really was, 2018–2026" side={<div className="gw-chips gw-chips-inline"><button type="button" className={`gw-chip${view === 'gazetted' ? ' gw-chip-active' : ''}`} onClick={() => setView('gazetted')}>Gazetted</button><button type="button" className={`gw-chip${view === 'all' ? ' gw-chip-active' : ''}`} onClick={() => setView('all')}>All incl. restricted</button></div>}>
        <div className="gw-card gw-table-card">
          <div className="gw-table-scroll">
            <table className="gw-table">
              <thead><tr><th>Holiday</th><th>Type</th><th>Avg effect</th><th>%</th><th>Afternoon</th><th>Evening</th><th>Years</th><th>Next</th></tr></thead>
              <tbody>{holidays.map((h) => (
                <tr key={h.name}>
                  <td className="gw-name">{h.name}</td><td>{h.gazetted ? 'Gazetted' : 'Restricted'}</td>
                  <td className={h.effect_mw < 0 ? 'gw-down' : 'gw-up'}>{h.effect_mw > 0 ? '+' : ''}{mw(h.effect_mw)} MW</td><td>{h.effect_pct > 0 ? '+' : ''}{h.effect_pct}%</td>
                  <td>{mw(h.afternoon_effect_mw)}</td><td>{mw(h.evening_effect_mw)}</td><td>{h.occurrences}</td><td>{h.next_date ? shortDate(h.next_date) : '—'}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      </Section>

      <Section title="Weather: temperature" note="Average demand in the latest year if every hour of the season had this temperature">
        <div className="dm-slopes">{data.weather.temperature.map((t, i) => <div className="gw-card dm-metric" key={t.season}><p>{t.season}</p><strong style={{ color: SEASON_COLORS[i] }}>{t.mw_per_degree != null ? `+${mw(t.mw_per_degree)} MW` : '—'}</strong><span>per °C around a typical {t.typical_c}°C</span></div>)}</div>
        <div className="gw-card gw-chart-card"><XYLineChart xLabel="Temperature (°C)" series={data.weather.temperature.map((t, i) => ({ name: t.season, color: SEASON_COLORS[i], points: t.curve.map((c) => ({ x: c.x, y: c.load_mw })) }))} /></div>
      </Section>

      <div className="gw-two">
        <Section title="Weather: rain" note="Rain in the previous 24 h">
          <div className="gw-card gw-chart-card"><XYLineChart xLabel="Rain in last 24 h (mm)" height={260} series={data.weather.rain.map((r, i) => ({ name: r.season, color: SEASON_COLORS[i], points: r.curve.map((c) => ({ x: c.x, y: c.load_mw })) }))} /></div>
        </Section>
        <Section title="Weather: humidity" note="Relative humidity, same hour">
          <div className="gw-card gw-chart-card"><XYLineChart xLabel="Humidity (%)" height={260} series={data.weather.humidity.map((r, i) => ({ name: r.season, color: SEASON_COLORS[i], points: r.curve.map((c) => ({ x: c.x, y: c.load_mw })) }))} /></div>
        </Section>
      </div>
    </div>
  );
}
