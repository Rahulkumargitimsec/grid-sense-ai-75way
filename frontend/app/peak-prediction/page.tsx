import { ModulePage } from '../../components/ModulePage';
import { OperationsWorkspace } from '../../components/OperationsWorkspace';
import { DemandOutlook } from '../../components/demand/Demand';

export default function PeakPredictionPage() {
  return <ModulePage eyebrow="Peak intelligence" title="High & low demand forecast" description="When Delhi's demand will run high or low over the next 7 days, how likely it is, and the reasons behind each peak and minimum." cards={[]} sectionTitle="Short-term baseline" sectionDescription="The original next-24-hour peak estimate from imported datasets, kept for comparison." actionLabel="Explain a past day" actionHref="/ai-explanation" before={<div className="gw-dashboard-live"><DemandOutlook /></div>}><OperationsWorkspace kind="peak" /></ModulePage>;
}
