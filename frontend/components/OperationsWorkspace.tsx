'use client';

import { useEffect, useMemo, useState } from 'react';
import { useAuth } from './AuthProvider';
import { ChartPoint, DemandChart } from './DemandChart';

type WorkspaceKind = 'dashboard' | 'forecast' | 'peak' | 'training' | 'comparison';
type ForecastPoint = { forecast_for: string; demand_mw: number; confidence: number };
type ForecastData = { model_name: string; horizon: number; forecasts: ForecastPoint[]; metrics: Record<string, number>; data_points: number; last_observed_at: string; last_observed_demand_mw: number; used_fallback: boolean; cadence_minutes: number ; data_source: DataSource };
type SummaryData = { model_name: string; next_forecast: ForecastPoint; metrics: Record<string, number>; data_points: number; last_observed_at: string; last_observed_demand_mw: number; used_fallback: boolean ; data_source: DataSource };
type PeakData = { model_name: string; peak_for: string; peak_demand_mw: number; horizon: number; confidence: number; metrics: Record<string, number>; used_fallback: boolean ; data_source: DataSource };
type Alert = { id: number; title: string; severity: string; message: string; acknowledged: boolean; created_at: string };
type Comparison = { model_name: string; metrics: Record<string, number>; forecast_demand_mw: number; horizon?: string | null; evaluated_on?: string | null };
type DataSource = 'sldc' | 'imported' | 'sample';
type ModelRun = { id: number; model_name: string; algorithm: string; status: string; metrics: Record<string, number>; data_points: number; trained_at: string; created_by: string };

const modelOptions = ['gridsense_ai', 'weighted_ensemble', 'persistence', 'moving_average', 'trend'];
const MODEL_LABELS: Record<string, string> = {
  gridsense_ai: 'GridSense AI (weather + calendar)', weighted_ensemble: 'Weighted ensemble (baseline)', persistence: 'Persistence (baseline)',
  moving_average: 'Moving average (baseline)', trend: 'Trend (baseline)',
};
const modelLabel = (name: string) => MODEL_LABELS[name] ?? name.replaceAll('_', ' ');
const SOURCE_LABELS: Record<DataSource, string> = { sldc: 'Delhi SLDC live data', imported: 'Imported dataset', sample: 'Demo series' };
const sourceLabel = (source: DataSource | undefined, usedFallback: boolean) => (source ? SOURCE_LABELS[source] : usedFallback ? 'Demo series' : 'Imported data');

async function request<T>(path: string, accessToken: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { Authorization: `Bearer ${accessToken}`, ...(init?.headers || {}) } });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === 'string' ? payload.detail : 'The workspace request failed.');
  }
  return response.json() as Promise<T>;
}

function formatDate(value: string) {
  return new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function chartLabel(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function toChartPoints(points: { forecast_for: string; demand_mw: number; confidence: number }[]): ChartPoint[] {
  return points.map((point) => ({ label: chartLabel(point.forecast_for), value: point.demand_mw, confidence: point.confidence }));
}

function WorkspaceSkeleton({ chart }: { chart?: boolean }) {
  return (
    <div className="workspace-skeleton" role="status" aria-label="Loading workspace data">
      <div className="metric-grid">{[0, 1, 2, 3].map((key) => <div className="skeleton-card" key={key}><span className="skeleton-line skeleton-line-sm" /><span className="skeleton-line skeleton-line-lg" /><span className="skeleton-line skeleton-line-sm" /></div>)}</div>
      <div className="skeleton-panel">{chart && <span className="skeleton-chart" />}<span className="skeleton-line" /><span className="skeleton-line" /><span className="skeleton-line skeleton-line-short" /></div>
    </div>
  );
}

function MetricCards({ cards }: { cards: { label: string; value: string; detail: string; tone: string }[] }) {
  return <div className="metric-grid">{cards.map((card) => <article className={`metric-card metric-card-${card.tone}`} key={card.label}><p>{card.label}</p><strong>{card.value}</strong><span>{card.detail}</span></article>)}</div>;
}

function WorkspaceState({ error, loading, chart }: { error: string; loading: boolean; chart?: boolean }) {
  if (loading) return <WorkspaceSkeleton chart={chart} />;
  if (error) return <div className="workspace-state workspace-state-error" role="alert">{error}</div>;
  return null;
}

function DashboardWorkspace({ accessToken }: { accessToken: string }) {
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [peak, setPeak] = useState<PeakData | null>(null);
  const [series, setSeries] = useState<ForecastData | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      request<SummaryData>('/api/v1/forecast/summary', accessToken),
      request<PeakData>('/api/v1/peak-prediction', accessToken),
      request<Alert[]>('/api/v1/alerts?state=active&acknowledged=false', accessToken),
      request<ForecastData>('/api/v1/forecast/horizon?horizon=24&model_name=gridsense_ai', accessToken)
    ]).then(([summaryResponse, peakResponse, alertsResponse, seriesResponse]) => {
      setSummary(summaryResponse);
      setPeak(peakResponse);
      setAlerts(alertsResponse);
      setSeries(seriesResponse);
    }).catch((requestError) => setError(requestError instanceof Error ? requestError.message : 'Unable to load dashboard data.')).finally(() => setLoading(false));
  }, [accessToken]);

  return <>
    <WorkspaceState error={error} loading={loading} chart />
    {summary && peak && <>
      <MetricCards cards={[{ label: 'Current load', value: `${summary.last_observed_demand_mw.toFixed(1)} MW`, detail: 'Latest validated observation', tone: 'blue' }, { label: 'Next forecast', value: `${summary.next_forecast.demand_mw.toFixed(1)} MW`, detail: formatDate(summary.next_forecast.forecast_for), tone: 'green' }, { label: 'Expected peak', value: `${peak.peak_demand_mw.toFixed(1)} MW`, detail: formatDate(peak.peak_for), tone: 'amber' }, { label: 'Confidence', value: `${Math.round(peak.confidence * 100)}%`, detail: `${modelLabel(summary.model_name)} model`, tone: 'slate' }]} />
      {series && series.forecasts.length > 1 && <div className="data-table-panel chart-panel"><div className="panel-heading"><div><h2>Next 24 hours</h2><p>Projected demand from the {modelLabel(series.model_name)} model.</p></div><span className="panel-badge">{sourceLabel(series.data_source, series.used_fallback)}</span></div><DemandChart points={toChartPoints(series.forecasts)} height={240} /></div>}
      <div className="operations-grid"><section className="data-table-panel"><div className="panel-heading"><div><h2>Forecast health</h2><p>{summary.data_points} observations used for this output.</p></div><span className="panel-badge">{sourceLabel(summary.data_source, summary.used_fallback)}</span></div><div className="forecast-health-list"><span><strong>MAE</strong>{summary.metrics.mae?.toFixed(2)} MW</span><span><strong>RMSE</strong>{summary.metrics.rmse?.toFixed(2)} MW</span><span><strong>MAPE</strong>{summary.metrics.mape?.toFixed(2)}%</span><span><strong>Peak error</strong>{summary.metrics.peak_magnitude_error_mw?.toFixed(2)} MW</span></div></section><section className="data-table-panel"><div className="panel-heading"><div><h2>Open alerts</h2><p>Signals requiring operator review.</p></div><span className={`alert-count${alerts.length === 0 ? ' alert-count-zero' : ''}`}>{alerts.length}</span></div>{alerts.length === 0 ? <p className="workspace-muted">No open alerts.</p> : <div className="alert-preview-list">{alerts.slice(0, 3).map((alert) => <div className="alert-preview-item" key={alert.id}><span className={`severity-dot severity-${alert.severity}`} /><div><strong>{alert.title}</strong><p>{alert.message}</p></div></div>)}</div>}</section></div>
    </>}
  </>;
}

function ForecastWorkspace({ accessToken }: { accessToken: string }) {
  const [model, setModel] = useState('gridsense_ai');
  const [horizon, setHorizon] = useState(24);
  const [data, setData] = useState<ForecastData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const maxDemand = useMemo(() => data ? Math.max(...data.forecasts.map((point) => point.demand_mw)) : 0, [data]);

  const loadForecast = async () => {
    setLoading(true);
    setError('');
    try {
      setData(await request<ForecastData>(`/api/v1/forecast/horizon?horizon=${horizon}&model_name=${model}`, accessToken));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Unable to load forecast.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadForecast(); }, [accessToken]);

  const exportForecast = () => {
    if (!data) return;
    const csv = ['forecast_for,demand_mw,confidence', ...data.forecasts.map((point) => `${point.forecast_for},${point.demand_mw},${point.confidence}`)].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `gridsense-${model}-forecast.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return <>
    <div className="workspace-toolbar"><label>Model<select value={model} onChange={(event) => setModel(event.target.value)}>{modelOptions.map((option) => <option key={option} value={option}>{modelLabel(option)}</option>)}</select></label><label>Horizon<select value={horizon} onChange={(event) => setHorizon(Number(event.target.value))}><option value="24">24 hours</option><option value="48">48 hours</option><option value="72">72 hours</option><option value="168">7 days (GridSense AI)</option></select></label><button className="secondary-button" type="button" onClick={() => void loadForecast()}>Refresh forecast</button><button className="primary-button" type="button" onClick={exportForecast} disabled={!data}>Export CSV</button></div>
    <WorkspaceState error={error} loading={loading} chart />
    {data && <><MetricCards cards={[{ label: 'Forecast horizon', value: `${data.horizon} hours`, detail: `${data.cadence_minutes}-minute cadence`, tone: 'blue' }, { label: 'Expected peak', value: `${maxDemand.toFixed(1)} MW`, detail: 'Within selected horizon', tone: 'amber' }, { label: 'Confidence', value: `${Math.round((data.forecasts[0]?.confidence || 0) * 100)}%`, detail: 'Current model output', tone: 'green' }, { label: 'Model', value: modelLabel(data.model_name), detail: sourceLabel(data.data_source, data.used_fallback), tone: 'slate' }]} /><div className="data-table-panel chart-panel"><div className="panel-heading"><div><h2>Demand curve</h2><p>Projected load across the selected horizon, with the expected peak marked.</p></div><span className="panel-badge">{data.horizon}-hour horizon</span></div><DemandChart points={toChartPoints(data.forecasts)} /></div><div className="data-table-panel"><div className="panel-heading"><div><h2>Hourly demand projection</h2><p>Confidence and demand values for every forecast point.</p></div><span className="panel-badge">{data.data_points} observations</span></div><div className="forecast-table-wrap"><table className="forecast-table"><thead><tr><th>Time</th><th>Demand</th><th>Confidence</th><th>Relative load</th></tr></thead><tbody>{data.forecasts.map((point) => <tr key={point.forecast_for}><td>{formatDate(point.forecast_for)}</td><td><strong>{point.demand_mw.toFixed(1)} MW</strong></td><td>{Math.round(point.confidence * 100)}%</td><td><meter min="0" max={maxDemand} value={point.demand_mw} /></td></tr>)}</tbody></table></div></div></>}
  </>;
}

function PeakWorkspace({ accessToken }: { accessToken: string }) {
  const [data, setData] = useState<PeakData | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { request<PeakData>('/api/v1/peak-prediction', accessToken).then(setData).catch((requestError) => setError(requestError instanceof Error ? requestError.message : 'Unable to load peak prediction.')); }, [accessToken]);
  return <><WorkspaceState error={error} loading={!data && !error} />{data && <><MetricCards cards={[{ label: 'Predicted peak', value: `${data.peak_demand_mw.toFixed(1)} MW`, detail: 'Highest forecast point', tone: 'amber' }, { label: 'Peak timing', value: new Date(data.peak_for).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), detail: new Date(data.peak_for).toLocaleDateString(), tone: 'blue' }, { label: 'Probability', value: `${Math.round(data.confidence * 100)}%`, detail: `${data.horizon}-hour horizon`, tone: 'green' }, { label: 'Magnitude error', value: `${data.metrics.peak_magnitude_error_mw?.toFixed(1)} MW`, detail: modelLabel(data.model_name), tone: 'slate' }]} /><div className="data-table-panel"><div className="panel-heading"><div><h2>Peak risk assessment</h2><p>Use timing, magnitude, and confidence together before taking action.</p></div><span className="panel-badge">{data.confidence >= 0.8 ? 'High confidence' : 'Review confidence'}</span></div><div className="peak-assessment"><div><strong>{data.metrics.peak_timing_error_hours?.toFixed(1)} hours</strong><span>Backtest timing error</span></div><div><strong>{sourceLabel(data.data_source, data.used_fallback)}</strong><span>Source status</span></div><div><strong>{modelLabel(data.model_name)}</strong><span>Selected model</span></div></div></div></>}</>;
}

function ModelWorkspace({ accessToken, comparison }: { accessToken: string; comparison: boolean }) {
  const [model, setModel] = useState('gridsense_ai');
  const [models, setModels] = useState<Comparison[]>([]);
  const [run, setRun] = useState<ModelRun | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const loadComparison = async () => { setLoading(true); try { const data = await request<{ models: Comparison[] }>('/api/v1/models/comparison', accessToken); setModels(data.models); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : 'Unable to load model comparison.'); } finally { setLoading(false); } };
  useEffect(() => { if (comparison) void loadComparison(); }, [accessToken, comparison]);
  const train = async () => { setLoading(true); setError(''); try { setRun(await request<ModelRun>('/api/v1/models/train', accessToken, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model_name: model }) })); } catch (requestError) { setError(requestError instanceof Error ? requestError.message : 'Unable to train model.'); } finally { setLoading(false); } };
  return <><div className="workspace-toolbar"><label>Model<select value={model} onChange={(event) => setModel(event.target.value)}>{modelOptions.map((option) => <option key={option} value={option}>{modelLabel(option)}</option>)}</select></label>{!comparison && <button className="primary-button" type="button" onClick={() => void train()} disabled={loading}>{loading ? 'Training...' : 'Start training run'}</button>}{comparison && <button className="secondary-button" type="button" onClick={() => void loadComparison()} disabled={loading}>Refresh comparison</button>}</div><WorkspaceState error={error} loading={loading} />{run && <div className="training-result"><p className="eyebrow">Training complete</p><h3>{modelLabel(run.model_name)} model run #{run.id}</h3><p>{run.data_points} observations evaluated with a {run.metrics.mape?.toFixed(2)}% MAPE.</p></div>}{comparison && <div className="data-table-panel"><div className="panel-heading"><div><h2>Evaluation matrix</h2><p>Errors in MW on real Delhi load. GridSense AI is scored a week ahead on a held-out year; baselines only one hour ahead, which is a much easier task.</p></div><span className="panel-badge">{models.length} models</span></div><div className="forecast-table-wrap"><table className="forecast-table"><thead><tr><th>Model</th><th>Horizon</th><th>Next hour</th><th>MAE</th><th>RMSE</th><th>MAPE</th><th>Peak error</th><th>Evaluated on</th></tr></thead><tbody>{models.map((item) => <tr key={item.model_name}><td><strong>{modelLabel(item.model_name)}</strong></td><td>{item.horizon ?? '1 step ahead'}</td><td>{item.forecast_demand_mw.toFixed(1)} MW</td><td>{item.metrics.mae?.toFixed(2)}</td><td>{item.metrics.rmse?.toFixed(2)}</td><td>{item.metrics.mape?.toFixed(2)}%</td><td>{item.metrics.peak_magnitude_error_mw?.toFixed(2)} MW</td><td>{item.evaluated_on ?? '—'}</td></tr>)}</tbody></table></div></div>}</>;
}

export function OperationsWorkspace({ kind }: { kind: WorkspaceKind }) {
  const { accessToken } = useAuth();
  if (!accessToken) return <div className="workspace-state">Waiting for an authenticated session...</div>;
  if (kind === 'dashboard') return <DashboardWorkspace accessToken={accessToken} />;
  if (kind === 'forecast') return <ForecastWorkspace accessToken={accessToken} />;
  if (kind === 'peak') return <PeakWorkspace accessToken={accessToken} />;
  return <ModelWorkspace accessToken={accessToken} comparison={kind === 'comparison'} />;
}
