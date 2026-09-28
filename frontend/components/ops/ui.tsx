'use client';

/* Small shared pieces for the operations pages (alerts, recommendations, settings, profile, analytics). */

export const mw = (value: number | null | undefined) => (value == null ? '—' : `${Math.round(value).toLocaleString('en-IN')} MW`);

export function inr(value: number | null | undefined) {
  if (value == null) return '—';
  if (value >= 1e7) return `₹${(value / 1e7).toFixed(2)} Cr`;
  if (value >= 1e5) return `₹${(value / 1e5).toFixed(1)} L`;
  return `₹${Math.round(value).toLocaleString('en-IN')}`;
}

/** Backend timestamps with a zone are UTC; naive ones are IST wall time. */
export function toDate(iso: string) {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}+05:30`);
}

export function timeAgo(iso: string | null | undefined) {
  if (!iso) return '—';
  const seconds = Math.round((Date.now() - toDate(iso).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

export function when(iso: string | null | undefined) {
  if (!iso) return '—';
  return toDate(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });
}

export const errorText = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

export function Section({ title, note, side, children }: { title: string; note?: string; side?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="gw-section">
      <div className="gw-section-head"><div><h2>{title}</h2>{note && <p className="gw-stamp">{note}</p>}</div>{side && <div className="gw-section-side">{side}</div>}</div>
      {children}
    </section>
  );
}

export function Stat({ label, value, detail, tone }: { label: string; value: React.ReactNode; detail?: React.ReactNode; tone?: 'critical' | 'high' | 'ok' | 'muted' }) {
  return (
    <div className={`gw-card dm-metric ops-stat${tone ? ` ops-stat-${tone}` : ''}`}>
      <p>{label}</p><strong>{value}</strong>{detail && <span>{detail}</span>}
    </div>
  );
}

export function Chips<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string; count?: number }[]; onChange: (value: T) => void; label: string }) {
  return (
    <div className="gw-chips gw-chips-inline" role="group" aria-label={label}>
      {options.map((option) => (
        <button key={option.value} type="button" className={`gw-chip${value === option.value ? ' gw-chip-active' : ''}`} onClick={() => onChange(option.value)} aria-pressed={value === option.value}>
          {option.label}{option.count != null && <span className="ops-chip-count">{option.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Loading({ error, what }: { error?: string; what: string }) {
  if (error) return <div className="gw-card gw-empty gw-bad" role="alert">{error}</div>;
  return <div className="gw-card gw-empty" role="status">Loading {what}…</div>;
}
