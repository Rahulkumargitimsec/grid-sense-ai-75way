'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { Role, useAuth, User } from './AuthProvider';
import { Icon, IconName } from './Icons';
import { ThemeToggle } from './ThemeToggle';
import { LiveClock } from './grid/LiveGrid';
import { useAlertSummary } from './ops/Alerts';

type NavigationItem = { label: string; href: string; icon: IconName; roles?: Role[] };

const navigation: NavigationItem[] = [
  { label: 'Live Grid', href: '/live-grid', icon: 'peak' },
  { label: 'Dashboard', href: '/dashboard', icon: 'dashboard', roles: ['super_admin', 'grid_operator'] },
  { label: 'Forecast', href: '/forecast', icon: 'forecast', roles: ['super_admin', 'grid_operator'] },
  { label: 'Peak Prediction', href: '/peak-prediction', icon: 'peak', roles: ['super_admin', 'grid_operator'] },
  { label: 'AI Explanation', href: '/ai-explanation', icon: 'ai', roles: ['super_admin', 'grid_operator', 'research_analyst'] },
  { label: 'Recommendations', href: '/recommendations', icon: 'recommendations', roles: ['super_admin', 'grid_operator'] },
  { label: 'Alerts', href: '/alerts', icon: 'alerts', roles: ['super_admin', 'grid_operator'] },
  { label: 'Analytics', href: '/analytics', icon: 'analytics', roles: ['super_admin', 'grid_operator', 'research_analyst'] },
  { label: 'Datasets', href: '/datasets', icon: 'datasets', roles: ['super_admin', 'research_analyst'] },
  { label: 'Model Training', href: '/model-training', icon: 'training', roles: ['super_admin', 'research_analyst'] },
  { label: 'Model Comparison', href: '/model-comparison', icon: 'comparison', roles: ['super_admin', 'research_analyst'] },
  { label: 'Research', href: '/research', icon: 'research', roles: ['super_admin', 'research_analyst'] },
  { label: 'Reports', href: '/reports', icon: 'reports', roles: ['super_admin', 'grid_operator', 'research_analyst'] }
];

const accountNavigation: NavigationItem[] = [
  { label: 'Profile', href: '/profile', icon: 'profile' },
  { label: 'Settings', href: '/settings', icon: 'settings' },
  { label: 'Admin Panel', href: '/admin', icon: 'admin', roles: ['super_admin'] }
];

const roleLabels: Record<Role, string> = {
  super_admin: 'Super Admin',
  grid_operator: 'Grid Operator',
  research_analyst: 'Research Analyst'
};

function initials(user: User) {
  return user.display_name.split(' ').map((part) => part[0]).slice(0, 2).join('').toUpperCase();
}

function NavigationLink({ label, href, icon, onNavigate }: { label: string; href: string; icon: IconName; onNavigate: () => void }) {
  const pathname = usePathname();
  const active = pathname === href;

  return (
    <Link className={`navigation-link${active ? ' navigation-link-active' : ''}`} href={href} onClick={onNavigate}>
      <span className="navigation-marker" aria-hidden="true"><Icon name={icon} /></span>
      <span>{label}</span>
    </Link>
  );
}

export function AppShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { user, loading, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const closeSidebar = () => setSidebarOpen(false);

  useEffect(() => {
    if (!loading && !user) {
      const currentPath = pathname || '/dashboard';
      const returnTo = currentPath.startsWith('/') && !currentPath.startsWith('//') ? currentPath : '/dashboard';
      router.replace(`/login?returnTo=${encodeURIComponent(returnTo)}`);
    }
  }, [loading, pathname, router, user]);

  const handleLogout = async () => {
    await logout();
    router.replace('/login');
  };

  const alertSummary = useAlertSummary(!!user && user.role !== 'research_analyst');

  if (loading || !user) {
    return <div className="auth-loading-screen" role="status">Loading GridSense workspace...</div>;
  }

  const visibleNavigation = navigation.filter((item) => !item.roles || item.roles.includes(user.role));
  const visibleAccountNavigation = accountNavigation.filter((item) => !item.roles || item.roles.includes(user.role));

  return (
    <div className="app-frame">
      <button className="mobile-menu-button" type="button" onClick={() => setSidebarOpen(true)} aria-label="Open navigation">
        <span /><span /><span />
      </button>
      {sidebarOpen && <button className="sidebar-backdrop" type="button" onClick={closeSidebar} aria-label="Close navigation" />}
      <aside className={`app-sidebar${sidebarOpen ? ' app-sidebar-open' : ''}`}>
        <div className="sidebar-brand">
          <span className="brand-mark">GS</span>
          <span><strong>GridSense</strong><small>Delhi power grid</small></span>
          <button className="sidebar-close-button" type="button" onClick={closeSidebar} aria-label="Close navigation">×</button>
        </div>
        <div className="navigation-group">
          <p className="navigation-heading">Operations</p>
          {visibleNavigation.map((item) => <NavigationLink key={item.href} {...item} onNavigate={closeSidebar} />)}
        </div>
        <div className="navigation-group navigation-account-group">
          <p className="navigation-heading">Account</p>
          {visibleAccountNavigation.map((item) => <NavigationLink key={item.href} {...item} onNavigate={closeSidebar} />)}
        </div>
        <div className="sidebar-status">
          <span className="status-indicator" />
          <span><strong>SCADA link online</strong><small>Delhi SLDC · refreshes every minute</small></span>
        </div>
      </aside>
      <div className="app-content">
        <header className="app-header">
          <div><p className="header-context">GridSense AI / Operations</p><p className="header-location">Delhi Power Grid</p></div>
          <div className="header-actions">
            {pathname !== '/live-grid' && <span className="header-live"><LiveClock /></span>}
            <ThemeToggle />
            {user.role !== 'research_analyst' && <Link className="header-alert-link" href="/alerts" title={alertSummary ? `${alertSummary.unacknowledged} unacknowledged, ${alertSummary.for_me} matching your preferences` : undefined}>Alerts <span className={`alert-count${alertSummary && alertSummary.for_me === 0 ? ' alert-count-zero' : ''}${alertSummary?.critical ? ' alert-count-critical' : ''}`}>{alertSummary ? alertSummary.for_me : '…'}</span></Link>}
            <Link className="user-menu" href="/profile">
              <span className="user-avatar">{initials(user)}</span>
              <span><strong>{user.display_name}</strong><small>{roleLabels[user.role]}</small></span>
            </Link>
            <button className="logout-button" type="button" onClick={() => void handleLogout()}>Log out</button>
          </div>
        </header>
        <main className="page-content">{children}</main>
      </div>
    </div>
  );
}

export { roleLabels };
