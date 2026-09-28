'use client';

import { useMemo, useState } from 'react';

/* ------------------------------------------------------------------------------------------------
   Frequency dial. IEGC band (49.90–50.05 Hz) is the normal operating window; the red flags mark the
   edges of the dial range where the grid is under real stress.
   ------------------------------------------------------------------------------------------------ */

const DIAL_MIN = 49.7;
const DIAL_MAX = 50.3;
const IEGC_LOW = 49.9;
const IEGC_HIGH = 50.05;

function dialPoint(value: number, radius: number, cx = 160, cy = 160) {
  const clamped = Math.min(DIAL_MAX, Math.max(DIAL_MIN, value));
  const angle = Math.PI * (1.15 - 1.3 * ((clamped - DIAL_MIN) / (DIAL_MAX - DIAL_MIN)));
  return { x: cx + radius * Math.cos(angle), y: cy - radius * Math.sin(angle) };
}

function arcPath(from: number, to: number, radius: number) {
  const a = dialPoint(from, radius);
  const b = dialPoint(to, radius);
  const large = (to - from) / (DIAL_MAX - DIAL_MIN) * 1.3 > 1 ? 1 : 0;
  return `M${a.x.toFixed(1)} ${a.y.toFixed(1)} A${radius} ${radius} 0 ${large} 1 ${b.x.toFixed(1)} ${b.y.toFixed(1)}`;
}

export function FrequencyGauge({ hz }: { hz: number | null | undefined }) {
  const value = hz ?? 50;
  const needle = dialPoint(value, 108);
  const inBand = hz != null && hz >= IEGC_LOW && hz <= IEGC_HIGH;
  const ticks = [49.7, 49.8, 49.9, 50.0, 50.1, 50.2, 50.3];
  return (
    <div className="gw-gauge">
      <svg viewBox="0 0 320 230" role="img" aria-label={`Grid frequency ${hz?.toFixed(2) ?? 'unavailable'} hertz`}>
        <path d={arcPath(DIAL_MIN, DIAL_MAX, 128)} className="gw-gauge-track" />
        <path d={arcPath(IEGC_LOW, IEGC_HIGH, 128)} className="gw-gauge-band" />
        {ticks.map((tick) => {
          const outer = dialPoint(tick, 128);
          const inner = dialPoint(tick, 116);
          const label = dialPoint(tick, 146);
          return (
            <g key={tick}>
              <line x1={inner.x} y1={inner.y} x2={outer.x} y2={outer.y} className="gw-gauge-tick" />
              <text x={label.x} y={label.y + 4} textAnchor="middle" className="gw-gauge-label">{tick.toFixed(1)}</text>
            </g>
          );
        })}
        {[DIAL_MIN + 0.02, DIAL_MAX - 0.08].map((edge) => {
          const p = dialPoint(edge, 128);
          return <rect key={edge} x={p.x - 7} y={p.y - 2} width="14" height="20" rx="2" className="gw-gauge-flag" />;
        })}
        <line x1="160" y1="160" x2={needle.x} y2={needle.y} className="gw-gauge-needle" style={{ transition: 'all 600ms ease' }} />
        <circle cx="160" cy="160" r="7" className="gw-gauge-hub" />
      </svg>
      <div className="gw-gauge-value">
        <strong>{hz != null ? hz.toFixed(2) : '--.--'}</strong><span>Hz</span>
      </div>
      <p className={`gw-gauge-status ${inBand ? 'gw-ok' : 'gw-warn'}`}>{hz == null ? 'Waiting for SCADA' : inBand ? 'Within IEGC band' : hz < IEGC_LOW ? 'Below IEGC band' : 'Above IEGC band'}</p>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
   Shared chart geometry
   ------------------------------------------------------------------------------------------------ */

const W = 1000;

function niceTicks(min: number, max: number, count = 4) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { lo: 0, hi: 1, ticks: [0, 1] };
  if (max === min) { max += 1; min -= 1; }
  const raw = (max - min) / count;
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v * 100) / 100);
  return { lo, hi, ticks };
}

/* ------------------------------------------------------------------------------------------------
   Multi-series line chart (daily load curve)
   ------------------------------------------------------------------------------------------------ */

export type LineSeries = { name: string; color: string; values: (number | null)[] };

export function MultiLineChart({ times, series, height = 340, unit = 'MW' }: { times: string[]; series: LineSeries[]; height?: number; unit?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const pad = { top: 18, right: 18, bottom: 34, left: 64 };
  const plotW = W - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const geometry = useMemo(() => {
    const all = series.flatMap((s) => s.values.filter((v): v is number => v != null));
    const { lo, hi, ticks } = niceTicks(Math.min(0, ...all), Math.max(...all, 1));
    // Always lay the day out on a 00:00–23:55 axis so a partial day (today) fills from the left.
    const slot = (t: string) => { const [h, m] = t.split(':').map(Number); return (h * 60 + m) / 1435; };
    const x = (i: number) => pad.left + slot(times[i]) * plotW;
    const y = (v: number) => pad.top + plotH - ((v - lo) / (hi - lo)) * plotH;
    const paths = series.map((s) => {
      let d = '';
      let pen = false;
      s.values.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
        pen = true;
      });
      return { ...s, d };
    });
    return { lo, hi, ticks, x, y, paths };
  }, [times, series, plotW, plotH]);

  if (!times.length) return <div className="gw-empty">No load data for this day yet.</div>;

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * W;
    let best = 0;
    let bestDistance = Infinity;
    times.forEach((_, i) => { const d = Math.abs(geometry.x(i) - px); if (d < bestDistance) { bestDistance = d; best = i; } });
    setHover(best);
  };

  const hourLabels = ['00:00', '03:00', '06:00', '09:00', '12:00', '15:00', '18:00', '21:00', '23:55'];
  return (
    <div className="gw-chart">
      <svg viewBox={`0 0 ${W} ${height}`} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img" aria-label="Load curve">
        {geometry.ticks.map((tick) => (
          <g key={tick}>
            <line x1={pad.left} x2={W - pad.right} y1={geometry.y(tick)} y2={geometry.y(tick)} className="gw-grid-line" />
            <text x={pad.left - 10} y={geometry.y(tick) + 4} textAnchor="end" className="gw-axis">{Math.round(tick).toLocaleString('en-IN')}</text>
          </g>
        ))}
        {hourLabels.map((label) => {
          const [h, m] = label.split(':').map(Number);
          const px = pad.left + ((h * 60 + m) / 1435) * plotW;
          return <text key={label} x={px} y={height - 10} textAnchor="middle" className="gw-axis">{label}</text>;
        })}
        {geometry.paths.map((s) => <path key={s.name} d={s.d} className="gw-line" style={{ stroke: s.color, filter: `drop-shadow(0 0 3px ${s.color})` }} />)}
        {hover != null && (
          <g>
            <line x1={geometry.x(hover)} x2={geometry.x(hover)} y1={pad.top} y2={pad.top + plotH} className="gw-cursor" />
            {series.map((s) => s.values[hover] != null && <circle key={s.name} cx={geometry.x(hover)} cy={geometry.y(s.values[hover] as number)} r="4" fill={s.color} />)}
          </g>
        )}
      </svg>
      <div className="gw-legend">
        {series.map((s) => (
          <span key={s.name}><i style={{ background: s.color }} />{s.name}{hover != null && s.values[hover] != null && <b>{Math.round(s.values[hover] as number).toLocaleString('en-IN')} {unit}</b>}</span>
        ))}
        {hover != null && <span className="gw-legend-time">{times[hover]} IST</span>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
   Peak demand vs weather: bars for daily peak (left axis, MW), lines for temperature and humidity
   (right axis, °C / %).
   ------------------------------------------------------------------------------------------------ */

export type PeakWeatherDay = { date: string; peak_mw: number | null; peak_time: string | null; temp_max_c: number | null; temp_mean_c: number | null; humidity_mean_pct: number | null; rain_mm: number | null };

export function PeakWeatherChart({ days, height = 360 }: { days: PeakWeatherDay[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const pad = { top: 20, right: 58, bottom: 38, left: 66 };
  const plotW = W - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const g = useMemo(() => {
    const peaks = days.map((d) => d.peak_mw).filter((v): v is number => v != null);
    const left = niceTicks(Math.min(...peaks, 8000) * 0.7, Math.max(...peaks, 1000));
    const right = niceTicks(0, 100, 5);
    const band = plotW / Math.max(days.length, 1);
    const x = (i: number) => pad.left + band * i + band / 2;
    const yL = (v: number) => pad.top + plotH - ((v - left.lo) / (left.hi - left.lo)) * plotH;
    const yR = (v: number) => pad.top + plotH - ((v - right.lo) / (right.hi - right.lo)) * plotH;
    const line = (key: 'temp_max_c' | 'humidity_mean_pct') => {
      let d = '';
      let pen = false;
      days.forEach((day, i) => {
        const v = day[key];
        if (v == null) { pen = false; return; }
        d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${yR(v).toFixed(1)}`;
        pen = true;
      });
      return d;
    };
    return { left, right, band, x, yL, yR, temp: line('temp_max_c'), humidity: line('humidity_mean_pct') };
  }, [days, plotW, plotH]);

  if (!days.length) return <div className="gw-empty">No daily peaks collected yet.</div>;
  const active = hover != null ? days[hover] : null;
  const every = Math.max(1, Math.ceil(days.length / 10));

  return (
    <div className="gw-chart">
      <div className="gw-legend gw-legend-top">
        <span><i className="gw-swatch-bar" />Peak demand</span>
        <span><i style={{ background: 'var(--gw-orange)' }} />Max temperature</span>
        <span><i style={{ background: 'var(--gw-cyan)' }} />Humidity</span>
      </div>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Daily peak demand with temperature and humidity" onMouseLeave={() => setHover(null)}>
        {g.left.ticks.map((tick) => (
          <g key={`l${tick}`}>
            <line x1={pad.left} x2={W - pad.right} y1={g.yL(tick)} y2={g.yL(tick)} className="gw-grid-line" />
            <text x={pad.left - 10} y={g.yL(tick) + 4} textAnchor="end" className="gw-axis">{Math.round(tick).toLocaleString('en-IN')}</text>
          </g>
        ))}
        {g.right.ticks.map((tick) => <text key={`r${tick}`} x={W - pad.right + 10} y={g.yR(tick) + 4} className="gw-axis">{tick}</text>)}
        <text x={18} y={pad.top + plotH / 2} className="gw-axis-title" transform={`rotate(-90 18 ${pad.top + plotH / 2})`} textAnchor="middle">MW</text>
        <text x={W - 12} y={pad.top + plotH / 2} className="gw-axis-title" transform={`rotate(90 ${W - 12} ${pad.top + plotH / 2})`} textAnchor="middle">°C / %</text>
        {days.map((day, i) => (
          <g key={day.date} onMouseEnter={() => setHover(i)}>
            <rect x={pad.left + g.band * i} y={pad.top} width={g.band} height={plotH} fill="transparent" />
            {day.peak_mw != null && <rect x={g.x(i) - g.band * 0.3} width={g.band * 0.6} y={g.yL(day.peak_mw)} height={pad.top + plotH - g.yL(day.peak_mw)} rx="3" className={`gw-bar${hover === i ? ' gw-bar-active' : ''}`} />}
            {i % every === 0 && <text x={g.x(i)} y={height - 12} textAnchor="middle" className="gw-axis">{day.date.slice(8, 10)}/{day.date.slice(5, 7)}</text>}
          </g>
        ))}
        <path d={g.temp} className="gw-line" style={{ stroke: 'var(--gw-orange)' }} />
        <path d={g.humidity} className="gw-line gw-line-dashed" style={{ stroke: 'var(--gw-cyan)' }} />
      </svg>
      <div className="gw-readout" role="status">
        {active ? <>
          <strong>{new Date(`${active.date}T00:00:00`).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' })}</strong>
          <span>Peak <b>{active.peak_mw != null ? `${Math.round(active.peak_mw).toLocaleString('en-IN')} MW` : '—'}</b>{active.peak_time && <> at {active.peak_time}</>}</span>
          <span>Max <b>{active.temp_max_c ?? '—'}°C</b></span>
          <span>Humidity <b>{active.humidity_mean_pct ?? '—'}%</b></span>
          <span>Rain <b>{active.rain_mm ?? '—'} mm</b></span>
        </> : <span className="gw-muted">Hover a day to compare demand with the weather.</span>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
   Schedule vs actual bars (generation by plant)
   ------------------------------------------------------------------------------------------------ */

export function ScheduleActualBars({ rows, limit = 12 }: { rows: { entity: string; schedule_mw: number | null; actual_mw: number | null }[]; limit?: number }) {
  const shown = rows.slice(0, limit);
  const max = Math.max(1, ...shown.flatMap((r) => [r.schedule_mw ?? 0, r.actual_mw ?? 0]));
  return (
    <div className="gw-sa-list">
      {shown.map((row) => {
        const deviation = (row.actual_mw ?? 0) - (row.schedule_mw ?? 0);
        return (
          <div className="gw-sa-row" key={row.entity}>
            <span className="gw-sa-name">{row.entity}</span>
            <span className="gw-sa-track">
              <span className="gw-sa-schedule" style={{ width: `${((row.schedule_mw ?? 0) / max) * 100}%` }} />
              <span className="gw-sa-actual" style={{ width: `${((row.actual_mw ?? 0) / max) * 100}%` }} />
            </span>
            <span className="gw-sa-value">{Math.round(row.actual_mw ?? 0).toLocaleString('en-IN')}</span>
            <span className={`gw-sa-dev ${deviation >= 0 ? 'gw-ok' : 'gw-bad'}`}>{deviation >= 0 ? '+' : ''}{Math.round(deviation)}</span>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
   Forecast band chart: predicted load with its 80% band, actuals so far, and high / low marks.
   Hours predicted as high or low are tinted so "when" reads at a glance.
   ------------------------------------------------------------------------------------------------ */

export type BandPoint = { at: string; predicted_mw: number; low_band_mw: number; high_band_mw: number; actual_mw: number | null; is_high: boolean; is_low: boolean; probability_high: number; probability_low: number; temperature_c: number | null };

export function ForecastBandChart({ points, highMw, lowMw, height = 360, selectedDate, onSelectDate }: { points: BandPoint[]; highMw: number; lowMw: number; height?: number; selectedDate?: string; onSelectDate?: (date: string) => void }) {
  const [hover, setHover] = useState<number | null>(null);
  const pad = { top: 20, right: 20, bottom: 40, left: 66 };
  const plotW = W - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const g = useMemo(() => {
    const values = points.flatMap((p) => [p.low_band_mw, p.high_band_mw, p.actual_mw ?? p.predicted_mw]).concat([highMw, lowMw]);
    const { lo, hi, ticks } = niceTicks(Math.min(...values) * 0.97, Math.max(...values) * 1.02);
    const x = (i: number) => pad.left + (points.length <= 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
    const y = (v: number) => pad.top + plotH - ((v - lo) / (hi - lo)) * plotH;
    const line = (pick: (p: BandPoint) => number | null) => {
      let d = '';
      let pen = false;
      points.forEach((p, i) => { const v = pick(p); if (v == null) { pen = false; return; } d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`; pen = true; });
      return d;
    };
    const band = points.length ? `${points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(p.high_band_mw).toFixed(1)}`).join(' ')} ${[...points].reverse().map((p, k) => `L${x(points.length - 1 - k).toFixed(1)} ${y(p.low_band_mw).toFixed(1)}`).join(' ')} Z` : '';
    const days: { date: string; from: number; to: number }[] = [];
    points.forEach((p, i) => { const date = p.at.slice(0, 10); const last = days[days.length - 1]; if (last && last.date === date) last.to = i; else days.push({ date, from: i, to: i }); });
    return { ticks, x, y, predicted: line((p) => p.predicted_mw), actual: line((p) => p.actual_mw), band, days };
  }, [points, highMw, lowMw, plotW, plotH]);

  if (points.length < 2) return <div className="gw-empty">No forecast hours available.</div>;
  const step = plotW / Math.max(1, points.length - 1);
  const active = hover != null ? points[hover] : null;

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * W;
    setHover(Math.max(0, Math.min(points.length - 1, Math.round((px - pad.left) / step))));
  };

  return (
    <div className="gw-chart">
      <div className="gw-legend gw-legend-top">
        <span><i style={{ background: 'var(--gw-yellow)' }} />Predicted</span>
        <span><i className="gw-swatch-band" />80% range</span>
        <span><i style={{ background: 'var(--gw-white)' }} />Actual so far</span>
        <span><i style={{ background: 'var(--gw-red)' }} />High mark {Math.round(highMw).toLocaleString('en-IN')} MW</span>
        <span><i style={{ background: 'var(--gw-teal)' }} />Low mark {Math.round(lowMw).toLocaleString('en-IN')} MW</span>
      </div>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Seven-day demand forecast" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {g.days.map((day) => (
          <g key={day.date} onClick={() => onSelectDate?.(day.date)} style={{ cursor: onSelectDate ? 'pointer' : undefined }}>
            <rect x={g.x(day.from) - step / 2} y={pad.top} width={(day.to - day.from + 1) * step} height={plotH} className={day.date === selectedDate ? 'gw-day-selected' : 'gw-day-hit'} />
            <line x1={g.x(day.from) - step / 2} x2={g.x(day.from) - step / 2} y1={pad.top} y2={pad.top + plotH} className="gw-grid-line" />
            <text x={g.x(Math.round((day.from + day.to) / 2))} y={height - 12} textAnchor="middle" className="gw-axis">{new Date(`${day.date}T00:00:00`).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric' })}</text>
          </g>
        ))}
        {points.map((p, i) => (p.is_high || p.is_low) && <rect key={p.at} x={g.x(i) - step / 2} y={pad.top} width={step} height={plotH} className={p.is_high ? 'gw-tint-high' : 'gw-tint-low'} pointerEvents="none" />)}
        {g.ticks.map((tick) => (
          <g key={tick} pointerEvents="none">
            <line x1={pad.left} x2={W - pad.right} y1={g.y(tick)} y2={g.y(tick)} className="gw-grid-line" />
            <text x={pad.left - 10} y={g.y(tick) + 4} textAnchor="end" className="gw-axis">{Math.round(tick).toLocaleString('en-IN')}</text>
          </g>
        ))}
        <path d={g.band} className="gw-band" pointerEvents="none" />
        <line x1={pad.left} x2={W - pad.right} y1={g.y(highMw)} y2={g.y(highMw)} className="gw-mark gw-mark-high" />
        <line x1={pad.left} x2={W - pad.right} y1={g.y(lowMw)} y2={g.y(lowMw)} className="gw-mark gw-mark-low" />
        <path d={g.predicted} className="gw-line" style={{ stroke: 'var(--gw-yellow)' }} pointerEvents="none" />
        <path d={g.actual} className="gw-line" style={{ stroke: 'var(--gw-white)' }} pointerEvents="none" />
        {active && hover != null && (
          <g pointerEvents="none">
            <line x1={g.x(hover)} x2={g.x(hover)} y1={pad.top} y2={pad.top + plotH} className="gw-cursor" />
            <circle cx={g.x(hover)} cy={g.y(active.predicted_mw)} r="4.5" fill="var(--gw-yellow)" />
          </g>
        )}
      </svg>
      <div className="gw-readout" role="status">
        {active ? <>
          <strong>{new Date(active.at).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' })} {active.at.slice(11, 16)}</strong>
          <span>Predicted <b>{Math.round(active.predicted_mw).toLocaleString('en-IN')} MW</b> ({Math.round(active.low_band_mw).toLocaleString('en-IN')}–{Math.round(active.high_band_mw).toLocaleString('en-IN')})</span>
          {active.actual_mw != null && <span>Actual <b>{Math.round(active.actual_mw).toLocaleString('en-IN')} MW</b></span>}
          <span>Chance high <b>{Math.round(active.probability_high * 100)}%</b></span>
          <span>Chance low <b>{Math.round(active.probability_low * 100)}%</b></span>
          {active.temperature_c != null && <span>Temp <b>{active.temperature_c}°C</b></span>}
        </> : <span className="gw-muted">Red-tinted hours are expected to be high, teal-tinted hours low. Click a day for its reasons.</span>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
   Numeric x/y lines (weather response curves)
   ------------------------------------------------------------------------------------------------ */

export function XYLineChart({ series, xLabel, yLabel = 'MW', height = 300, markers }: { series: { name: string; color: string; points: { x: number; y: number }[] }[]; xLabel: string; yLabel?: string; height?: number; markers?: { x: number; label: string }[] }) {
  const pad = { top: 16, right: 20, bottom: 44, left: 66 };
  const plotW = W - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const all = series.flatMap((s) => s.points);
  if (!all.length) return <div className="gw-empty">No data.</div>;
  const xs = niceTicks(Math.min(...all.map((p) => p.x)), Math.max(...all.map((p) => p.x)), 6);
  const ys = niceTicks(Math.min(...all.map((p) => p.y)), Math.max(...all.map((p) => p.y)));
  const x = (v: number) => pad.left + ((v - xs.lo) / (xs.hi - xs.lo)) * plotW;
  const y = (v: number) => pad.top + plotH - ((v - ys.lo) / (ys.hi - ys.lo)) * plotH;
  return (
    <div className="gw-chart">
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`${yLabel} by ${xLabel}`}>
        {ys.ticks.map((t) => <g key={`y${t}`}><line x1={pad.left} x2={W - pad.right} y1={y(t)} y2={y(t)} className="gw-grid-line" /><text x={pad.left - 10} y={y(t) + 4} textAnchor="end" className="gw-axis">{Math.round(t).toLocaleString('en-IN')}</text></g>)}
        {xs.ticks.map((t) => <text key={`x${t}`} x={x(t)} y={height - 22} textAnchor="middle" className="gw-axis">{t}</text>)}
        <text x={pad.left + plotW / 2} y={height - 4} textAnchor="middle" className="gw-axis-title">{xLabel}</text>
        {markers?.map((m) => <g key={m.label}><line x1={x(m.x)} x2={x(m.x)} y1={pad.top} y2={pad.top + plotH} className="gw-cursor" /><text x={x(m.x) + 4} y={pad.top + 12} className="gw-axis-title">{m.label}</text></g>)}
        {series.map((s) => <path key={s.name} d={s.points.map((p, i) => `${i ? 'L' : 'M'}${x(p.x).toFixed(1)} ${y(p.y).toFixed(1)}`).join(' ')} className="gw-line" style={{ stroke: s.color }} />)}
        {series.map((s) => s.points.map((p) => <circle key={`${s.name}${p.x}`} cx={x(p.x)} cy={y(p.y)} r="3" fill={s.color} />))}
      </svg>
      <div className="gw-legend">{series.map((s) => <span key={s.name}><i style={{ background: s.color }} />{s.name}</span>)}</div>
    </div>
  );
}
