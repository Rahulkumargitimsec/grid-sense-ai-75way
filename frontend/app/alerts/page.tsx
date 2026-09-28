import { ModulePage } from '../../components/ModulePage';
import { AlertsCenter } from '../../components/ops/Alerts';

export default function AlertsPage() {
  return <ModulePage eyebrow="Operator attention" title="Alerts" description="Demand forecast warnings, live load, frequency, overdrawal, voltage, peak records and data-quality problems, checked every 5 minutes and cleared automatically when conditions recover." cards={[]} sectionTitle="" sectionDescription="" actionLabel="Tune alert limits" actionHref="/settings" bare><AlertsCenter /></ModulePage>;
}
