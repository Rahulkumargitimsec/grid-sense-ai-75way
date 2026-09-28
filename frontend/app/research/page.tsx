import { AdvancedWorkspace } from '../../components/AdvancedWorkspace';
import { ModulePage } from '../../components/ModulePage';

export default function ResearchPage() {
  return <ModulePage eyebrow="Research workspace" title="Research" description="Organize datasets, experiments, model comparisons, and reproducibility metadata for publication-ready analysis." cards={[]} sectionTitle="Experiment history" sectionDescription="Create experiments, run deterministic comparisons, and retain data fingerprints with each result." actionLabel="Compare models" actionHref="/model-comparison"><AdvancedWorkspace kind="research" /></ModulePage>;
}
