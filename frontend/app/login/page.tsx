'use client';

import Link from 'next/link';
import { FormEvent, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../components/AuthProvider';
import { ThemeToggle } from '../../components/ThemeToggle';

const demoAccounts = [
  { role: 'Super Admin', email: 'admin@gridsense.ai', password: 'GridSenseAdmin!2025' },
  { role: 'Grid Operator', email: 'operator@gridsense.ai', password: 'GridSenseOperator!2025' },
  { role: 'Research Analyst', email: 'analyst@gridsense.ai', password: 'GridSenseAnalyst!2025' }
];

function safeReturnPath(value: string | null) {
  return value && value.startsWith('/') && !value.startsWith('//') ? value : '/dashboard';
}

export default function LoginPage() {
  const router = useRouter();
  const { user, loading, login } = useAuth();
  const [email, setEmail] = useState('operator@gridsense.ai');
  const [password, setPassword] = useState('GridSenseOperator!2025');
  const [returnTo, setReturnTo] = useState('/dashboard');
  const [explicitReturn, setExplicitReturn] = useState(false);
  const signingIn = useRef(false);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get('returnTo');
    setReturnTo(safeReturnPath(requested));
    setExplicitReturn(Boolean(requested && requested !== '/dashboard'));
  }, []);

  useEffect(() => {
    if (!loading && user && !signingIn.current) {
      router.replace(returnTo);
    }
  }, [loading, router, user, returnTo]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError('');
    setSubmitting(true);
    signingIn.current = true;
    try {
      const token = await login({ email, password });
      let destination = returnTo;
      // Saved preferences decide the start page and theme, unless the user was sent here from a specific page.
      try {
        const response = await fetch('/api/v1/users/me/preferences', { headers: { Authorization: `Bearer ${token}` } });
        if (response.ok) {
          const preferences = await response.json() as { landing_page?: string; theme?: 'dark' | 'light' };
          if (!explicitReturn && preferences.landing_page) destination = safeReturnPath(preferences.landing_page);
          if (preferences.theme) {
            document.documentElement.dataset.theme = preferences.theme;
            try { window.localStorage.setItem('gridsense-theme', preferences.theme); } catch { /* storage unavailable */ }
          }
        }
      } catch { /* preferences are optional */ }
      router.replace(destination);
      router.refresh();
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : 'Unable to sign in');
    } finally {
      signingIn.current = false;
      setSubmitting(false);
    }
  };

  return (
    <main className="login-page">
      <div className="login-layout">
        <section className="login-intro">
          <Link className="landing-brand" href="/"><span className="brand-mark">GS</span><span><strong>GridSense AI</strong><small>Delhi power grid intelligence</small></span></Link>
          <div className="login-intro-top"><p className="eyebrow">Secure operations workspace</p><ThemeToggle /></div>
          <h1>Make the next grid decision with confidence.</h1>
          <p>Sign in to access role-aware forecasting, peak prediction, research, and administration workspaces.</p>
          <Link className="text-link" href="/">Back to public site <span>→</span></Link>
        </section>
        <section className="login-card" aria-labelledby="login-title">
          <p className="eyebrow">Welcome back</p>
          <h2 id="login-title">Sign in to GridSense</h2>
          <p className="login-subtitle">Use one of the demo accounts below to explore the Phase 2 workspace.</p>
          <form className="login-form" onSubmit={handleSubmit}>
            <label htmlFor="email">Email address</label>
            <input id="email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
            <label htmlFor="password">Password</label>
            <input id="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="primary-button login-submit-button" type="submit" disabled={submitting}>{submitting ? 'Signing in...' : 'Sign in'}</button>
          </form>
          <div className="demo-credentials">
            <h3>Demo credentials</h3>
            {demoAccounts.map((account) => <button className="credential-row" type="button" key={account.email} onClick={() => { setEmail(account.email); setPassword(account.password); }}><span><strong>{account.role}</strong><small>{account.email}</small></span><code>{account.password}</code></button>)}
          </div>
          <p className="login-security-note">Tokens stay in memory and an HttpOnly refresh cookie. No credentials are stored in local storage.</p>
        </section>
      </div>
    </main>
  );
}
