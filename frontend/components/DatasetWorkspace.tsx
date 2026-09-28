'use client';

import { ChangeEvent, useEffect, useState } from 'react';
import { useAuth } from './AuthProvider';

type DatasetPreview = {
  file_name: string;
  columns: string[];
  sample_rows: Record<string, string>[];
  row_count: number;
  valid_rows: number;
  invalid_rows: number;
  errors: string[];
};

type DatasetImport = {
  id: number;
  dataset_name: string;
  version: number;
  file_name: string;
  status: string;
  row_count: number;
  valid_rows: number;
  invalid_rows: number;
  uploaded_at: string;
};

async function readError(response: Response) {
  const payload = await response.json().catch(() => null);
  if (typeof payload?.detail === 'string') return payload.detail;
  if (payload?.detail?.message) return payload.detail.message;
  return 'The dataset request could not be completed.';
}

export function DatasetWorkspace() {
  const { accessToken } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [datasetName, setDatasetName] = useState('Historical load');
  const [preview, setPreview] = useState<DatasetPreview | null>(null);
  const [imports, setImports] = useState<DatasetImport[]>([]);
  const [loading, setLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const authorizedHeaders: Record<string, string> = accessToken ? { Authorization: `Bearer ${accessToken}` } : {};

  const loadHistory = async () => {
    if (!accessToken) return;
    setHistoryLoading(true);
    try {
      const response = await fetch('/api/v1/datasets/imports', { headers: authorizedHeaders });
      if (!response.ok) throw new Error(await readError(response));
      setImports(await response.json() as DatasetImport[]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Unable to load import history.');
    } finally {
      setHistoryLoading(false);
    }
  };

  useEffect(() => {
    void loadHistory();
  }, [accessToken]);

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    setFile(event.target.files?.[0] || null);
    setPreview(null);
    setError('');
    setNotice('');
  };

  const requestPreview = async () => {
    if (!file || !accessToken) return;
    setLoading(true);
    setError('');
    setNotice('');
    const formData = new FormData();
    formData.append('file', file);
    try {
      const response = await fetch('/api/v1/datasets/imports/preview', { method: 'POST', body: formData, headers: authorizedHeaders });
      if (!response.ok) throw new Error(await readError(response));
      setPreview(await response.json() as DatasetPreview);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Unable to preview this CSV.');
    } finally {
      setLoading(false);
    }
  };

  const importFile = async () => {
    if (!file || !preview || preview.invalid_rows > 0 || !accessToken) return;
    setLoading(true);
    setError('');
    setNotice('');
    const formData = new FormData();
    formData.append('file', file);
    formData.append('dataset_name', datasetName);
    try {
      const response = await fetch('/api/v1/datasets/imports', { method: 'POST', body: formData, headers: authorizedHeaders });
      if (!response.ok) throw new Error(await readError(response));
      const created = await response.json() as DatasetImport;
      setNotice(`Version ${created.version} imported with ${created.valid_rows} validated rows.`);
      setFile(null);
      setPreview(null);
      await loadHistory();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Unable to import this CSV.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="dataset-workspace">
      <div className="dataset-upload-grid">
        <section className="dataset-upload-card">
          <div className="dataset-section-heading"><div><p className="eyebrow">Import a version</p><h3>Review data before storage</h3></div><span className="panel-badge">CSV only</span></div>
          <div className="dataset-form-fields">
            <div className="form-field"><label htmlFor="dataset-name">Dataset name</label><input id="dataset-name" value={datasetName} onChange={(event) => setDatasetName(event.target.value)} /></div>
            <div className="form-field"><label htmlFor="dataset-file">CSV file</label><input id="dataset-file" type="file" accept=".csv,text/csv" onChange={handleFileChange} /></div>
          </div>
          <p className="dataset-help">Required columns: <code>timestamp</code> and <code>demand_mw</code>. Aliases <code>recorded_at</code> and <code>load_mw</code> are also accepted.</p>
          <div className="dataset-actions"><button className="secondary-button dataset-button" type="button" onClick={() => void requestPreview()} disabled={!file || loading}>{loading ? 'Working...' : 'Preview CSV'}</button><button className="primary-button dataset-button" type="button" onClick={() => void importFile()} disabled={!preview || preview.invalid_rows > 0 || loading}>{loading ? 'Importing...' : 'Validate and import'}</button></div>
          {error && <p className="form-error" role="alert">{error}</p>}
          {notice && <p className="dataset-notice" role="status">{notice}</p>}
        </section>
        <section className="dataset-quality-card"><p className="eyebrow">Validation rules</p><h3>Clean inputs for forecasting</h3><ul className="dataset-rule-list"><li>UTF-8 CSV files up to 10 MB</li><li>ISO-formatted timestamps</li><li>Positive demand values in MW</li><li>Invalid rows block an import</li></ul></section>
      </div>
      {preview && <section className="dataset-preview-panel"><div className="dataset-section-heading"><div><p className="eyebrow">Preview</p><h3>{preview.file_name}</h3></div><div className="dataset-preview-summary"><strong>{preview.valid_rows}/{preview.row_count}</strong><span>valid rows</span></div></div><div className="dataset-stat-row"><span>Columns: {preview.columns.join(', ')}</span><span className={preview.invalid_rows ? 'dataset-invalid' : 'dataset-valid'}>{preview.invalid_rows ? `${preview.invalid_rows} invalid` : 'Ready to import'}</span></div>{preview.errors.length > 0 && <ul className="dataset-error-list">{preview.errors.map((item) => <li key={item}>{item}</li>)}</ul>}<div className="dataset-table-wrap"><table className="dataset-table"><thead><tr>{preview.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{preview.sample_rows.map((row, index) => <tr key={`${preview.file_name}-${index}`}>{preview.columns.map((column) => <td key={column}>{row[column] || '—'}</td>)}</tr>)}</tbody></table></div></section>}
      <section className="dataset-history-panel"><div className="dataset-section-heading"><div><p className="eyebrow">Import history</p><h3>Versioned dataset registry</h3></div><span className="panel-badge">{imports.length} versions</span></div>{historyLoading ? <p className="dataset-muted">Loading import history...</p> : imports.length === 0 ? <div className="dataset-empty"><span className="empty-state-marker">GS</span><p>No validated imports yet. Preview a CSV to create the first dataset version.</p></div> : <div className="dataset-table-wrap"><table className="dataset-table"><thead><tr><th>Dataset</th><th>Version</th><th>File</th><th>Rows</th><th>Status</th><th>Imported</th></tr></thead><tbody>{imports.map((item) => <tr key={item.id}><td>{item.dataset_name}</td><td>v{item.version}</td><td>{item.file_name}</td><td>{item.valid_rows}</td><td><span className="dataset-status">{item.status}</span></td><td>{new Date(item.uploaded_at).toLocaleString()}</td></tr>)}</tbody></table></div>}</section>
    </div>
  );
}
