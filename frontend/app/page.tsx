import Link from 'next/link';
import { DemoRequestForm } from '../components/DemoRequestForm';
import { Icon, IconName } from '../components/Icons';
import { ThemeToggle } from '../components/ThemeToggle';

const featureCards: { title: string; description: string; icon: IconName }[] = [
  { icon: 'forecast', title: 'Demand forecasting', description: 'Plan hourly load with clear confidence and peak timing signals.' },
  { icon: 'ai', title: 'Explainable intelligence', description: 'Understand which weather, calendar, and historical features influence each prediction.' },
  { icon: 'target', title: 'Operational action', description: 'Turn peak-risk signals into commercial load and power purchase recommendations.' }
];

const technologyCards = [
  { marker: '01', title: 'Reliable inputs', description: 'Load history, weather, calendar effects, and operational context come together in one modeling foundation.' },
  { marker: '02', title: 'Model ensemble', description: 'Compare candidate forecasting approaches and promote the model that earns trust from grid teams.' },
  { marker: '03', title: 'Human-readable output', description: 'Confidence bands, feature influence, and peak alerts keep every prediction ready for a real decision.' }
];

export default function HomePage() {
  return (
    <main className="landing-page">
      <nav className="landing-navigation" aria-label="Main navigation">
        <Link className="landing-brand" href="/"><span className="brand-mark">GS</span><span><strong>GridSense AI</strong><small>Delhi power grid intelligence</small></span></Link>
        <div className="landing-links"><a href="#features">Features</a><a href="#ai-engine">AI Engine</a><a href="#technology">Technology</a><a href="#architecture">Architecture</a><a href="#research">Research</a><a href="#contact">Contact</a><Link href="/login">Login</Link><ThemeToggle /></div>
      </nav>
      <section className="landing-hero">
        <div className="hero-copy"><p className="eyebrow">AI-powered smart grid decision support</p><h1>See tomorrow’s demand before the grid feels it.</h1><p className="hero-description">GridSense AI combines load history, weather, calendar context, and explainable forecasting to help Delhi power operators plan with confidence.</p><div className="hero-actions"><Link className="primary-button" href="/login">Explore dashboard</Link><a className="secondary-button" href="#request-demo">Request a demo</a><a className="secondary-button" href="/gridsense-ai-development-plan.pdf" download="gridsense-ai-development-plan.pdf">Download plan</a></div><div className="hero-proof"><span className="status-indicator" /><span>Forecast workspace ready for Phase 2</span></div></div>
        <div className="hero-visual" aria-label="Demand forecast preview"><div className="visual-header"><span>Tomorrow demand outlook</span><strong>+4.8%</strong></div><div className="forecast-bars"><span className="bar-38" /><span className="bar-52" /><span className="bar-46" /><span className="bar-70" /><span className="bar-61" /><span className="peak-bar bar-92" /><span className="bar-76" /><span className="bar-58" /></div><div className="visual-footer"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div></div>
      </section>
      <section className="feature-section" id="features"><div className="section-intro"><p className="eyebrow">One operational view</p><h2>From prediction to power-system action.</h2></div><div className="feature-grid">{featureCards.map((feature) => <article className="feature-card" key={feature.title}><span className="feature-marker" aria-hidden="true"><Icon name={feature.icon} /></span><h3>{feature.title}</h3><p>{feature.description}</p></article>)}</div></section>
      <section className="ai-engine-section" id="ai-engine"><div className="ai-engine-copy"><p className="eyebrow">The GridSense AI engine</p><h2>Signals in. Decisions out.</h2><p>Our forecasting workflow is designed around the questions grid teams actually ask: when will demand peak, why is it changing, and what should happen next?</p><Link className="secondary-button" href="/login">Open the workspace</Link></div><div className="engine-steps"><div><span>01</span><strong>Observe</strong><p>Bring together the signals that shape demand.</p></div><div><span>02</span><strong>Explain</strong><p>See the drivers behind every forecast.</p></div><div><span>03</span><strong>Act</strong><p>Share a confident operational response.</p></div></div></section>
      <section className="technology-section" id="technology"><div className="section-intro"><p className="eyebrow">Technology foundation</p><h2>Built for trustworthy grid intelligence.</h2></div><div className="technology-grid">{technologyCards.map((card) => <article className="technology-card" key={card.marker}><span>{card.marker}</span><h3>{card.title}</h3><p>{card.description}</p></article>)}</div></section>
      <section className="architecture-section" id="architecture"><div><p className="eyebrow">Built for grid teams</p><h2>A secure architecture that scales from signal to system.</h2></div><p>Operators get a focused dashboard, analysts get reproducible model comparisons, and administrators get a clear foundation for secure data and model operations. Phase 2 adds authenticated workspaces without coupling the product to a future domain schema.</p></section>
      <section className="research-section" id="research"><p className="eyebrow">Research-ready by design</p><h2>Move from an experiment to a decision system.</h2><p className="research-description">Validate models, understand uncertainty, and keep the path from research notebook to operational forecast visible to every stakeholder.</p><Link className="text-link" href="/login?returnTo=%2Fresearch">View research workspace <span>→</span></Link></section>
      <section className="contact-section" id="contact"><div className="contact-copy"><p className="eyebrow">Start a conversation</p><h2>Bring better visibility to your next peak.</h2><p>Tell us what your team needs to see sooner. We will tailor a walkthrough around your grid, your signals, and your decision cadence.</p><div className="contact-details"><span>hello@gridsense.ai</span><span>Delhi, India</span></div></div><div className="request-demo-card" id="request-demo"><p className="eyebrow">Request demo</p><h3>See GridSense in action.</h3><DemoRequestForm /></div></section>
      <footer className="landing-footer" id="footer"><span>GridSense AI</span><span>Intelligent electricity demand forecasting for Delhi</span></footer>
    </main>
  );
}
