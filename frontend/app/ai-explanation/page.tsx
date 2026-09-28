import { AdvancedWorkspace } from '../../components/AdvancedWorkspace';
import { ModulePage } from '../../components/ModulePage';
import { DemandExplainer } from '../../components/demand/Demand';

export default function AIExplanationPage() {
  return <ModulePage eyebrow="Explainable AI" title="Why demand was high or low" description="Pick any day since 2018, or one in the coming week, to see what drove its peak and its minimum, and see what every festival and holiday did to Delhi's demand year by year." cards={[]} sectionTitle="Forecast feature contributions" sectionDescription="MW contributions behind the next forecast hour." actionLabel="See the 7-day outlook" actionHref="/peak-prediction" before={<div className="gw-dashboard-live"><DemandExplainer /></div>}><AdvancedWorkspace kind="explanation" /></ModulePage>;
}
