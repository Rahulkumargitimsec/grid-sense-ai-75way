import { AdvancedWorkspace } from '../../components/AdvancedWorkspace';
import { ModulePage } from '../../components/ModulePage';

export default function AdminPage() {
  return <ModulePage eyebrow="Administration" title="Admin panel" description="Manage users, roles, system settings, and audit activity with backend-enforced controls." cards={[]} sectionTitle="Administration controls" sectionDescription="User activation, audit history, and persisted settings are restricted to Super Admins." actionLabel="Review settings" actionHref="/settings"><AdvancedWorkspace kind="admin" /></ModulePage>;
}
