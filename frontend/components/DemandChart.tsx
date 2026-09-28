'use client';

import { useId, useMemo, useState } from 'react';

export type ChartPoint = { label: string; value: number; confidence?: number };

type DemandChartProps = {
  points: ChartPoint[];
  unit?: string;
  height?: number;
  /** Highlights the highest point with a peak marker. */
  markPeak?: boolean;
};

const WIDTH = 860;
const PAD = { top: 26, right: 20, bottom: 30, left: 62 };

function niceBounds(min: number, max: number) {
  if (max === min) return { lo: min - 1, hi: max + 1 };
  const pad = (max - min) * 0.18;
  const step = Math.pow(10, Math.floor(Math.log10(max - min))) / 2;
  return { lo: Math.floor((min - pad) / step) * step, hi: Math.ceil((max + pad) / step) * step };
}

export function DemandChart({ points, unit = 'MW', height = 260, markPeak = true }: DemandChartProps) {
  const gradientId = useId();
  const [hover, setHover] = useState<number | null>(null);

  const model = useMemo(() => {
    const values = points.map((point) => point.value);
    const { lo, hi } = niceBounds(Math.min(...values), Math.max(...values));
    const plotW = WIDTH - PAD.left - PAD.right;
    const plotH = height - PAD.top - PAD.bottom;
    const x = (index: number) => PAD.left + (points.length === 1 ? plotW / 2 : (index / (points.length - 1)) * plotW);
    const y = (value: number) => PAD.top + plotH - ((value - lo) / (hi - lo)) * plotH;
    const coords = points.map((point, index) => ({ ...point, x: x(index), y: y(point.value), index }));
    const line = coords.map((c, i) => `${i === 0 ? 'M' : 'L'}${c.x.toFixed(1)} ${c.y.toFixed(1)}`).join(' ');
    const area = `${line} L${coords[coords.length - 1].x.toFixed(1)} ${PAD.top + plotH} L${coords[0].x.toFixed(1)} ${PAD.top + plotH} Z`;
    // A flat series (e.g. 119.0-119.3 MW) needs more decimals than a wide one,
    // otherwise every gridline prints the same rounded number.
    const span = hi - lo;
    const decimals = span >= 20 ? 0 : span >= 2 ? 1 : 2;
    const ticks = [0, 1, 2, 3].map((i) => {
      const value = lo + (span * i) / 3;
      return { value, y: y(value), label: value.toFixed(decimals) };
    });
    const peak = markPeak ? coords.reduce((best, c) => (c.value > best.value ? c : best), coords[0]) : null;
    const labelEvery = Math.max(1, Math.ceil(points.length / 7));
    return { coords, line, area, ticks, peak, plotH, labelEvery, decimals };
  }, [points, height, markPeak]);

  if (points.length < 2) return null;

  const active = hover === null ? null : model.coords[hover];

  const handleMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = ((event.clientX - rect.left) / rect.width) * WIDTH;
    const plotW = WIDTH - PAD.left - PAD.right;
    const index = Math.round(((ratio - PAD.left) / plotW) * (points.length - 1));
    setHover(Math.min(points.length - 1, Math.max(0, index)));
  };

  return (
    <div className="demand-chart">
      <svg
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={`Demand projection from ${points[0].label} to ${points[points.length - 1].label}`}
        onMouseMove={handleMove}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" className="chart-fill-top" />
            <stop offset="100%" className="chart-fill-bottom" />
          </linearGradient>
        </defs>

        {model.ticks.map((tick) => (
          <g key={tick.value}>
            <line className="chart-grid" x1={PAD.left} x2={WIDTH - PAD.right} y1={tick.y} y2={tick.y} />
            <text className="chart-axis-label" x={PAD.left - 10} y={tick.y + 4} textAnchor="end">{tick.label}</text>
          </g>
        ))}

        <path className="chart-area" d={model.area} fill={`url(#${gradientId})`} />
        <path className="chart-line" d={model.line} />

        {model.peak && (
          <g>
            <line className="chart-peak-line" x1={model.peak.x} x2={model.peak.x} y1={PAD.top} y2={PAD.top + model.plotH} />
            <circle className="chart-peak-dot" cx={model.peak.x} cy={model.peak.y} r="5" />
            <text className="chart-peak-label" x={Math.min(WIDTH - PAD.right - 52, Math.max(PAD.left + 52, model.peak.x))} y={PAD.top - 10} textAnchor="middle">PEAK {model.peak.value.toFixed(Math.max(1, model.decimals))} {unit}</text>
          </g>
        )}

        {model.coords.map((coord) => (
          coord.index % model.labelEvery === 0 ? (
            <text className="chart-axis-label chart-x-label" key={coord.index} x={coord.x} y={height - 10} textAnchor="middle">{coord.label}</text>
          ) : null
        ))}

        {active && (
          <g>
            <line className="chart-cursor" x1={active.x} x2={active.x} y1={PAD.top} y2={PAD.top + model.plotH} />
            <circle className="chart-cursor-dot" cx={active.x} cy={active.y} r="4.5" />
          </g>
        )}
      </svg>

      <div className="chart-readout" role="status">
        {active ? (
          <>
            <strong>{active.value.toFixed(1)} {unit}</strong>
            <span>{active.label}</span>
            {active.confidence !== undefined && <span className="chart-readout-confidence">{Math.round(active.confidence * 100)}% confidence</span>}
          </>
        ) : (
          <span className="chart-readout-hint">Hover the curve for demand at a point in time</span>
        )}
      </div>
    </div>
  );
}
