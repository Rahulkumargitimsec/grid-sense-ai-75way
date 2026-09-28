type IconProps = { className?: string };

const base = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  viewBox: '0 0 24 24',
  'aria-hidden': true
};

export const icons = {
  dashboard: (
    <svg {...base}><rect x="3" y="3" width="7.5" height="8.5" rx="1.6" /><rect x="13.5" y="3" width="7.5" height="5.5" rx="1.6" /><rect x="13.5" y="11.5" width="7.5" height="9.5" rx="1.6" /><rect x="3" y="14.5" width="7.5" height="6.5" rx="1.6" /></svg>
  ),
  forecast: (
    <svg {...base}><path d="M3 17.5 8.5 11l4 3.5L21 5" /><path d="M21 10V5h-5" /></svg>
  ),
  peak: (
    <svg {...base}><path d="M3 20h18" /><path d="m4 16 4.5-6 3.5 4 3-7 4 9" /><circle cx="15" cy="7" r="1.4" /></svg>
  ),
  ai: (
    <svg {...base}><path d="M12 3a3 3 0 0 0-3 3v.4A2.8 2.8 0 0 0 6.6 9 2.9 2.9 0 0 0 5 11.6c0 1 .5 1.9 1.3 2.4A2.9 2.9 0 0 0 9 18.1V18a3 3 0 0 0 6 0v.1a2.9 2.9 0 0 0 2.7-4.1 2.9 2.9 0 0 0 1.3-2.4A2.9 2.9 0 0 0 17.4 9 2.8 2.8 0 0 0 15 6.4V6a3 3 0 0 0-3-3Z" /><path d="M12 9v6" /></svg>
  ),
  recommendations: (
    <svg {...base}><path d="M9 18h6" /><path d="M10 21.5h4" /><path d="M12 2.5a6 6 0 0 0-3.6 10.8c.5.4.8.9.9 1.5l.1 1.2h5.2l.1-1.2c.1-.6.4-1.1.9-1.5A6 6 0 0 0 12 2.5Z" /></svg>
  ),
  alerts: (
    <svg {...base}><path d="M18 8.5a6 6 0 1 0-12 0c0 6-2 7.5-2 7.5h16s-2-1.5-2-7.5Z" /><path d="M13.7 20a2 2 0 0 1-3.4 0" /></svg>
  ),
  analytics: (
    <svg {...base}><path d="M3 3v16.5A1.5 1.5 0 0 0 4.5 21H21" /><rect x="7" y="11" width="3" height="6" rx="1" /><rect x="12.5" y="7.5" width="3" height="9.5" rx="1" /><rect x="18" y="13.5" width="3" height="3.5" rx="1" /></svg>
  ),
  datasets: (
    <svg {...base}><ellipse cx="12" cy="6" rx="8" ry="3.2" /><path d="M4 6v6c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2V6" /><path d="M4 12v6c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2v-6" /></svg>
  ),
  training: (
    <svg {...base}><circle cx="5.5" cy="6" r="2.2" /><circle cx="5.5" cy="18" r="2.2" /><circle cx="18.5" cy="12" r="2.2" /><path d="M7.6 7.1 16.4 11M7.6 16.9 16.4 13" /></svg>
  ),
  comparison: (
    <svg {...base}><path d="M12 3v18" /><path d="M6 7H3l3-4 3 4H6Zm0 0v5.5" /><path d="M18 17h3l-3 4-3-4h3Zm0 0v-5.5" /></svg>
  ),
  research: (
    <svg {...base}><circle cx="10.5" cy="10.5" r="6.5" /><path d="m20 20-4.8-4.8" /><path d="M8 10.5h5M10.5 8v5" /></svg>
  ),
  reports: (
    <svg {...base}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z" /><path d="M14 3v5h5" /><path d="M9 13h6M9 17h4" /></svg>
  ),
  profile: (
    <svg {...base}><circle cx="12" cy="8" r="3.6" /><path d="M4.5 20a7.5 7.5 0 0 1 15 0" /></svg>
  ),
  settings: (
    <svg {...base}><circle cx="12" cy="12" r="3" /><path d="M19.4 14.5a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.2a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.2a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.2a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.2a1.6 1.6 0 0 0-1.4 1Z" /></svg>
  ),
  admin: (
    <svg {...base}><path d="M12 2.8 4.5 6v5.6c0 4.6 3.1 8.8 7.5 9.6 4.4-.8 7.5-5 7.5-9.6V6L12 2.8Z" /><path d="m9.2 12 2 2 3.6-3.8" /></svg>
  ),
  spark: (
    <svg {...base}><path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12L13 2Z" /></svg>
  ),
  layers: (
    <svg {...base}><path d="m12 2.8 9 4.9-9 4.9-9-4.9 9-4.9Z" /><path d="m3.5 12.3 8.5 4.6 8.5-4.6" /><path d="m3.5 16.8 8.5 4.6 8.5-4.6" /></svg>
  ),
  target: (
    <svg {...base}><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r="1" /></svg>
  )
} as const;

export type IconName = keyof typeof icons;

export function Icon({ name }: { name: IconName } & IconProps) {
  return icons[name];
}
