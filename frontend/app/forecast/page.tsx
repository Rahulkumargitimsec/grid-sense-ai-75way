import { ModulePage } from '../../components/ModulePage';
import { OperationsWorkspace } from '../../components/OperationsWorkspace';

export default function ForecastPage() {
  return <ModulePage eyebrow="Forecasting" title="Hourly demand forecast" description="Select a forecast range and model to understand expected load, peak magnitude, peak timing, and confidence." cards={[]} sectionTitle="Forecast workspace" sectionDescription="Model, horizon, confidence, metrics, and export controls are connected to the forecasting API." actionLabel="Compare models" actionHref="/model-comparison"><OperationsWorkspace kind="forecast" /></ModulePage>;
}
