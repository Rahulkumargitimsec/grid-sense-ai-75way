import { DelhiLoadInsights } from '../../components/DelhiLoad';
import { ModulePage } from '../../components/ModulePage';
import { DemandInsights } from '../../components/demand/Demand';
import { AnalyticsHistory } from '../../components/ops/AnalyticsHistory';

export default function AnalyticsPage() {
  return <ModulePage eyebrow="Grid intelligence" title="Analytics" description="Nine years of real Delhi demand: growth, when power is used most, DISCOM shares, and how holidays and weather move load." cards={[]} sectionTitle="Synthetic Delhi load study (2023)" sectionDescription="Gradient-boosted model trained on 8,737 hourly readings from 2023, with effects measured by changing one factor at a time." actionLabel="Explain a day" actionHref="/ai-explanation" before={<div className="gw-dashboard-live"><AnalyticsHistory /><DemandInsights /></div>}><DelhiLoadInsights /></ModulePage>;
}
