import { AdvancedWorkspace } from '../../components/AdvancedWorkspace';
import { ModulePage } from '../../components/ModulePage';
import { OperationalSettings } from '../../components/ops/Settings';

export default function SettingsPage() {
  return <ModulePage eyebrow="Configuration" title="Settings" description="The limits and prices the forecast, alert engine and recommendations use. Changes apply within minutes." cards={[]} sectionTitle="Advanced: raw system settings" sectionDescription="Every stored key, including custom ones. Super Admins only." actionLabel="Open alerts" actionHref="/alerts" before={<div className="gw-dashboard-live"><OperationalSettings /></div>} adminOnlySection><AdvancedWorkspace kind="settings" /></ModulePage>;
}
