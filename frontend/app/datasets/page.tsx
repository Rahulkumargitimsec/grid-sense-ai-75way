import { DatasetWorkspace } from '../../components/DatasetWorkspace';
import { ModulePage } from '../../components/ModulePage';
import { DataSources } from '../../components/DataSources';

export default function DatasetsPage() {
  return <ModulePage eyebrow="Data foundation" title="Datasets" description="Manage historical load, weather, calendar, holiday, and festival data used by the forecasting pipeline." cards={[]} before={<div className="gw-dashboard-live"><DataSources /></div>} sectionTitle="Dataset registry" sectionDescription="Upload your own CSV load data: preview, validate, version, and review import history." actionLabel="Open training" actionHref="/model-training"><DatasetWorkspace /></ModulePage>;
}
