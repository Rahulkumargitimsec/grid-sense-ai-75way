import { ModulePage } from '../../components/ModulePage';
import { RecommendationsBoard } from '../../components/ops/Recommendations';

export default function RecommendationsPage() {
  return <ModulePage eyebrow="Decision support" title="Recommendations" description="Peak reduction, power purchase, DISCOM targets, maintenance windows and schedule revisions, worked out from the 7-day forecast and live drawl, with the MW and rupees at stake." cards={[]} sectionTitle="" sectionDescription="" actionLabel="Review peak risk" actionHref="/peak-prediction" bare><RecommendationsBoard /></ModulePage>;
}
