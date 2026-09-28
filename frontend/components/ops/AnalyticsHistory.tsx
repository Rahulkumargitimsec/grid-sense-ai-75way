'use client';

import { useEffect, useState } from 'react';
import { useApi } from '../api';
import { MultiLineChart } from '../grid/GridCharts';
import { errorText, Loading, mw, Section, Stat } from './ui';

type Year = { year: number; partial: boolean; coverage_pct: number; mean_mw: number; peak_mw: number; peak_at: string; min_mw: number; hours_above_high: number; hours_above_critical: number; energy_gwh: number; mean_growth_pct?: number; peak_growth_pct?: number };
type Overview = {
  data_until: string; thresholds: { high_mw: number; critical_mw: number; low_mw: number };
  yearly: Year[];
  year_to_date: { through: string; year: number; mean_mw: number; previous_mean_mw: number; mean_growth_pct: number; peak_mw: number; previous_peak_mw: number; hours_above_high: number; previous_hours_above_high: number } | null;
  financial_year_peaks: { financial_year: string; peak_mw: number; peak_at: string | null; precision: string }[];
  heatmap: { month: string; values: (number | null)[] }[];
  day_profiles: Record<string, (number | null)[]>;
  discom_shares: ({ year: number; mw: Record<string, number> } & Record<string, number>)[];
  peak_hour_distribution: { hour: number; days: number }[];
};

const DISCOM_COLORS: Record<string, string> = { BRPL: 'var(--gw-teal)', BYPL: 'var(--gw-orange)', TPDDL: 'var(--gw-sky)', NDMC: 'var(--gw-white)', MES: 'var(--gw-pink)' };
const PROFILE_COLORS: Record<string, string> = { Weekday: 'var(--gw-yellow)', Saturday: 'var(--gw-teal)', Sunday: 'var(--gw-sky)', Holiday: 'var(--gw-pink)' };
const signed = (value: number | undefined) => (value == null ? '' : `${value > 0 ? '+' : ''}${value.toFixed(1)}%`);

function YearBars({ years, high }: { years: Year[]; high: number }) {
  const max = Math.max(...years.map((year) => year.peak_mw));
  return (
    <div className="ops-years">
      {years.map((year) => (
        <div key={year.year} className="ops-year" title={`${year.year}: average ${mw(year.mean_mw)}, peak ${mw(year.peak_mw)} on ${year.peak_at.slice(0, 10)}`}>
          <div className="ops-year-bars">
            <span className="ops-year-peak" style={{ height: `${(year.peak_mw / max) * 100}%` }}><b>{(year.peak_mw / 1000).toFixed(1)}</b></span>
            <span className="ops-year-mean" style={{ height: `${(year.mean_mw / max) * 100}%` }}><b>{(year.mean_mw / 1000).toFixed(1)}</b></span>
            <i className="ops-year-high" style={{ bottom: `${(high / max) * 100}%` }} />
          </div>
          <strong>{year.year}{year.partial ? '*' : ''}</strong>
          <span className={year.mean_growth_pct != null && year.mean_growth_pct < 0 ? 'gw-down' : 'gw-up'}>{signed(year.mean_growth_pct)}</span>
        </div>
      ))}
    </div>
  );
}

function Heatmap({ rows }: { rows: Overview['heatmap'] }) {
  const values = rows.flatMap((row) => row.values).filter((value): value is number => value != null);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const colour = (value: number | null) => {
    if (value == null) return 'transparent';
    const t = (value - low) / (high - low || 1);
    // deep blue → teal → yellow → red, readable on both themes
    const stops = [[30, 64, 175], [13, 148, 136], [234, 179, 8], [220, 38, 38]];
    const position = t * (stops.length - 1);
    const i = Math.min(stops.length - 2, Math.floor(position));
    const f = position - i;
    const [r, g, b] = stops[i].map((channel, k) => Math.round(channel + (stops[i + 1][k] - channel) * f));
    return `rgb(${r} ${g} ${b})`;
  };
  return (
    <div className="ops-heatmap-wrap">
      <div className="ops-heatmap" role="table" aria-label="Average load by month and hour">
        <span />
        {Array.from({ length: 24 }, (_, hour) => <span key={hour} className="ops-heat-hour">{hour % 3 === 0 ? String(hour).padStart(2, '0') : ''}</span>)}
        {rows.map((row) => (
          <div key={row.month} className="ops-heat-row" role="row">
            <span className="ops-heat-month">{row.month}</span>
            {row.values.map((value, hour) => <span key={hour} className="ops-heat-cell" style={{ background: colour(value) }} title={`${row.month} ${String(hour).padStart(2, '0')}:00 · ${mw(value)}`} />)}
          </div>
        ))}
      </div>
      <div className="ops-heat-legend"><span>{mw(low)}</span><i /><span>{mw(high)}</span></div>
    </div>
  );
}

export function AnalyticsHistory() {
  const api = useApi();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { api<Overview>('/api/v1/analytics/overview').then(setData).catch((e) => setError(errorText(e, 'Unable to load historical analytics.'))); }, [api]);
  if (!data) return <Loading error={error} what="nine years of Delhi load" />;

  const ytd = data.year_to_date;
  const complete = data.yearly.filter((year) => !year.partial);
  const first = complete[0];
  const lastFull = complete[complete.length - 1];
  const cagr = first && lastFull ? (Math.pow(lastFull.mean_mw / first.mean_mw, 1 / (lastFull.year - first.year)) - 1) * 100 : null;
  const hours = Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, '0')}:00`);
  const maxPeakDays = Math.max(...data.peak_hour_distribution.map((row) => row.days), 1);
  const fy = data.financial_year_peaks.slice(-4);

  return (
    <div className="gw-board">
      <Section title="Nine years of Delhi demand" note={`Real SLDC SCADA load, 1 Jan 2018 to ${data.data_until.slice(0, 16).replace('T', ' ')} IST`}>
        <div className="dm-metrics">
          {ytd && <Stat label={`${ytd.year} so far vs ${ytd.year - 1}`} value={signed(ytd.mean_growth_pct)} detail={`Average ${mw(ytd.mean_mw)} vs ${mw(ytd.previous_mean_mw)}, 1 Jan – ${ytd.through}`} tone={ytd.mean_growth_pct > 0 ? 'high' : 'ok'} />}
          {ytd && <Stat label="Hours above the high mark" value={ytd.hours_above_high.toLocaleString('en-IN')} detail={`${ytd.previous_hours_above_high.toLocaleString('en-IN')} in the same period last year`} tone={ytd.hours_above_high > ytd.previous_hours_above_high ? 'critical' : 'ok'} />}
          {cagr != null && <Stat label={`Average growth ${first.year}–${lastFull.year}`} value={`${cagr.toFixed(1)}% / yr`} detail={`${mw(first.mean_mw)} → ${mw(lastFull.mean_mw)} average load`} />}
          {fy.length > 0 && <Stat label="Highest financial-year peak" value={mw(Math.max(...fy.map((row) => row.peak_mw)))} detail={fy.map((row) => `${row.financial_year.replace('FY ', '')} ${(row.peak_mw / 1000).toFixed(2)} GW`).join(' · ')} />}
        </div>
        <div className="gw-card gw-pad">
          <div className="gw-card-head"><p>Yearly peak (bright) and average load (GW) · dashed line = today&apos;s high mark</p><span className="gw-tag">* partial year</span></div>
          <YearBars years={data.yearly} high={data.thresholds.high_mw} />
        </div>
      </Section>

      <Section title="When Delhi uses the most power" note="Average load by month and hour over the last 12 months">
        <div className="gw-card gw-pad"><Heatmap rows={data.heatmap} /></div>
      </Section>

      <div className="gw-two">
        <Section title="Shape of the day" note="Average hourly load over the last 90 days">
          <div className="gw-card gw-chart-card">
            <MultiLineChart times={hours} height={280} series={Object.entries(data.day_profiles).map(([name, values]) => ({ name, color: PROFILE_COLORS[name], values }))} />
          </div>
        </Section>
        <Section title="When the daily peak happens" note="Hour of each day's highest load, last 12 months">
          <div className="gw-card gw-pad">
            <div className="ops-peak-hours">
              {data.peak_hour_distribution.map((row) => (
                <div key={row.hour} className="ops-peak-hour" title={`${row.days} days peaked at ${String(row.hour).padStart(2, '0')}:00`}>
                  <span style={{ height: `${(row.days / maxPeakDays) * 100}%` }} />
                  <small>{row.hour % 3 === 0 ? String(row.hour).padStart(2, '0') : ''}</small>
                </div>
              ))}
            </div>
            <p className="gw-footnote">Two peaks: late-morning in winter (heaters, offices), afternoon and late-night in summer and monsoon (air-conditioning).</p>
          </div>
        </Section>
      </div>

      <Section title="DISCOM share of Delhi load" note="Average share of the five distribution utilities by year">
        <div className="gw-card gw-pad ops-shares">
          {data.discom_shares.map((row) => (
            <div key={row.year} className="ops-share-row">
              <strong>{row.year}</strong>
              <div className="ops-share-bar">
                {Object.keys(DISCOM_COLORS).map((name) => <span key={name} style={{ width: `${row[name]}%`, background: DISCOM_COLORS[name] }} title={`${name}: ${row[name]}% (${mw(row.mw[name])} average)`}>{row[name] >= 8 ? `${name} ${row[name]}%` : ''}</span>)}
              </div>
            </div>
          ))}
          <div className="gw-legend">{Object.entries(DISCOM_COLORS).map(([name, color]) => <span key={name}><i style={{ background: color }} />{name}</span>)}</div>
        </div>
      </Section>

      <Section title="Year by year" note={`High mark ${mw(data.thresholds.high_mw)}, critical ${mw(data.thresholds.critical_mw)}`}>
        <div className="gw-card gw-table-card">
          <div className="gw-table-scroll">
            <table className="gw-table">
              <thead><tr><th>Year</th><th>Average</th><th>Growth</th><th>Peak</th><th>Peak on</th><th>Minimum</th><th>Hours ≥ high</th><th>Hours ≥ critical</th><th>Energy</th><th>Data</th></tr></thead>
              <tbody>{data.yearly.map((year) => (
                <tr key={year.year}>
                  <td className="gw-name">{year.year}{year.partial ? ' (so far)' : ''}</td><td>{mw(year.mean_mw)}</td>
                  <td className={year.mean_growth_pct != null && year.mean_growth_pct < 0 ? 'gw-down' : 'gw-up'}>{signed(year.mean_growth_pct) || '—'}</td>
                  <td>{mw(year.peak_mw)}</td><td>{year.peak_at.slice(0, 10)}</td><td>{mw(year.min_mw)}</td>
                  <td>{year.hours_above_high.toLocaleString('en-IN')}</td><td>{year.hours_above_critical.toLocaleString('en-IN')}</td>
                  <td>{year.energy_gwh.toLocaleString('en-IN')} GWh</td><td>{year.coverage_pct}%</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>
        <p className="gw-footnote">Peaks here are hourly averages, so they read a little below SLDC&apos;s instantaneous records. 2020 dips because of the COVID lockdown.</p>
      </Section>
    </div>
  );
}
