import { ModulePage } from '../../components/ModulePage';
import { ProfileCenter } from '../../components/ops/Profile';

export default function ProfilePage() {
  return <ModulePage eyebrow="Account" title="Your profile" description="Your role and access, preferences that follow you to any browser, password and recent activity." cards={[]} sectionTitle="" sectionDescription="" actionLabel="Open settings" actionHref="/settings" bare><ProfileCenter /></ModulePage>;
}
