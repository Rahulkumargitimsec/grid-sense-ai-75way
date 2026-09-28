import { AdvancedWorkspace } from '../../components/AdvancedWorkspace';
import { ModulePage } from '../../components/ModulePage';

export default function ReportsPage() {
  return <ModulePage eyebrow="Operational reporting" title="Reports" description="Generate decision-ready demand, peak, model performance, savings, and audit reports." cards={[]} sectionTitle="Report library" sectionDescription="Generate JSON or CSV reports and export persisted report content with author and timestamp metadata." actionLabel="View analytics" actionHref="/analytics"><AdvancedWorkspace kind="reports" /></ModulePage>;
}
