import Link from 'next/link';
import { ModulePage } from '../../components/ModulePage';
import { OperationsWorkspace } from '../../components/OperationsWorkspace';
import { LiveGridSummary } from '../../components/grid/LiveGrid';
import { DemandOutlookStrip } from '../../components/demand/Demand';

export default function DashboardPage() {
  return <ModulePage eyebrow="Operations overview" title="Grid operations dashboard" description="Live Delhi grid position from SLDC SCADA, tomorrow's expected demand, and the signals that need operator attention." cards={[]} sectionTitle="Demand forecast" sectionDescription="Forecast, peak, alert, and validation signals from the protected operations API." actionLabel="Open live grid" actionHref="/live-grid" before={<div className="gw-dashboard-live"><LiveGridSummary /><DemandOutlookStrip /><p><Link className="gw-dashboard-link" href="/live-grid">Substations, load curves, weather and generation on the full live board →</Link></p></div>}><OperationsWorkspace kind="dashboard" /></ModulePage>;
}
