import { useEffect, useState } from "react";
import { SystemStatusPanel } from "./SystemStatusPanel";
import { ScreenerPanel } from "./ScreenerPanel";
import { Activity, Bell, ChevronDown, CircleHelp, Command, LayoutDashboard, Menu, Moon, Search, Settings2, ShieldCheck, Sun, WalletCards, X } from "lucide-react";

type Theme = "dark" | "light";
const NAV_ITEMS = [
  { label: "Overview", description: "System overview", icon: LayoutDashboard },
  { label: "Market Screener", description: "Signals and market coverage", icon: Activity },
  { label: "Paper Trading", description: "Simulated orders and positions", icon: WalletCards },
  { label: "Settings", description: "Preferences and access", icon: Settings2 },
] as const;

function readInitialTheme(): Theme {
  try {
    const stored = window.localStorage.getItem("signal-bot-ui-theme");
    if (stored === "dark" || stored === "light") return stored;
  } catch { /* Storage may be unavailable in restricted browsing contexts. */ }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function App() {
  const [theme, setTheme] = useState<Theme>(readInitialTheme);
  const [activeView, setActiveView] = useState<string>("Overview");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    try { window.localStorage.setItem("signal-bot-ui-theme", theme); } catch { /* Current-session theme still applies. */ }
  }, [theme]);

  const toggleTheme = () => setTheme((current) => current === "dark" ? "light" : "dark");

  return <div className="app-frame">
    <a className="skip-link" href="#main-content">Skip to main content</a>
    {sidebarOpen && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
    <aside className={"sidebar " + (sidebarOpen ? "sidebar-open" : "")} aria-label="Dashboard sidebar">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true"><Activity size={20} strokeWidth={2.5} /></div>
        <div className="brand-copy"><strong>Signal Bot</strong><span>QUANTITATIVE PLATFORM</span></div>
        <button className="icon-button sidebar-close" aria-label="Close navigation" onClick={() => setSidebarOpen(false)}><X size={18} /></button>
      </div>
      <div className="workspace-label">WORKSPACE</div>
      <nav className="nav-list" aria-label="Primary navigation">
        {NAV_ITEMS.map(({ label, description, icon: Icon }) => <button key={label}
          className={"nav-item " + (activeView === label ? "nav-item-active" : "")}
          aria-current={activeView === label ? "page" : undefined} title={description}
          onClick={() => { setActiveView(label); setSidebarOpen(false); }}>
          <Icon size={18} aria-hidden="true" /><span>{label}</span>
          {label === "Market Screener" && <span className="nav-count">V2</span>}
        </button>)}
      </nav>
      <div className="sidebar-bottom">
        <div className="security-note"><ShieldCheck size={16} /><span>Server-controlled permissions</span></div>
        <div className="user-profile"><div className="avatar" aria-hidden="true">SB</div>
          <div className="user-meta"><strong>Preview workspace</strong><span>Frontend foundation</span></div><ChevronDown size={15} aria-hidden="true" />
        </div>
      </div>
    </aside>

    <div className="main-column">
      <header className="topbar">
        <div className="topbar-left">
          <button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><Menu size={20} /></button>
          <div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-separator">/</span><strong>{activeView}</strong></div>
        </div>
        <div className="topbar-actions">
          <div className="preview-status"><span className="status-dot" /> UI preview</div>
          <button className="icon-button" aria-label="Search (preview only)" title="Search (preview only)"><Search size={18} /></button>
          <button className="icon-button" aria-label="Notifications (preview only)" title="Notifications (preview only)"><Bell size={18} /></button>
          <span className="topbar-divider" />
          <button className="icon-button theme-toggle" onClick={toggleTheme} aria-label={"Switch to " + (theme === "dark" ? "light" : "dark") + " theme"} title="Switch theme">
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <button className="help-button" aria-label="Help (preview only)"><CircleHelp size={17} /><span>Help</span></button>
        </div>
      </header>

      <main id="main-content" className="main-content" tabIndex={-1}>
        <div className="page-heading"><div>
          <div className="eyebrow">SIGNAL BOT / FRONTEND FOUNDATION</div><h1>{activeView}</h1>
          <p className="page-description">A component-based interface is being built alongside the existing dashboard.</p>
        </div><div className="heading-actions"><button className="button button-secondary" onClick={() => setActiveView("Overview")}><Command size={16} /> Preview shell</button></div></div>

        <section className="migration-notice" aria-labelledby="preview-notice-title">
          <div className="notice-icon"><ShieldCheck size={20} /></div>
          <div><h2 id="preview-notice-title">Read-only Overview pilot</h2><p>The Overview reads the existing authenticated system-status endpoint only. It cannot start workers, change settings, place trades, or send Telegram alerts. Other sections remain preview-only; the current dashboard remains the default application.</p></div>
          <span className="notice-tag">READ ONLY</span>
        </section>

        {activeView === "Overview" ? <SystemStatusPanel /> : activeView === "Market Screener" ? <ScreenerPanel /> : <section className="panel preview-placeholder"><div className="panel-heading"><div><h2>{activeView} migration slice</h2><p>This view remains preview-only until its routes, permissions, schemas, and side effects have been verified.</p></div><span className="panel-counter">NOT CONNECTED</span></div><p className="empty-state">No live data or controls are exposed here yet. Existing dashboard functionality remains available in the legacy application.</p></section>}

        <section className="panel" aria-labelledby="roadmap-title">
          <div className="panel-heading"><div><h2 id="roadmap-title">Migration roadmap</h2><p>Small, testable slices with legacy fallback preserved.</p></div><span className="panel-counter">01 / 05</span></div>
          <ol className="roadmap-list">
            <li className="roadmap-complete"><span className="roadmap-step">00</span><div><strong>Inventory and parity plan</strong><p>Document existing views, endpoints, roles, and consequential side effects.</p></div><span className="roadmap-state">Documentation merged</span></li>
            <li className="roadmap-active"><span className="roadmap-step">01</span><div><strong>Frontend foundation</strong><p>React, TypeScript, local styling pipeline, accessible shell, and tests.</p></div><span className="roadmap-state">In progress</span></li>
            <li><span className="roadmap-step">02</span><div><strong>Read-only overview pilot</strong><p>Typed API client, loading/error states, and data-parity coverage.</p></div><span className="roadmap-state">Next</span></li>
            <li><span className="roadmap-step">03</span><div><strong>Market Screener V2</strong><p>Filters, table states, keyboard support, and responsive behavior.</p></div><span className="roadmap-state">Planned</span></li>
            <li><span className="roadmap-step">04</span><div><strong>Domain views and final parity</strong><p>Paper trading, strategies, wallets, ML, research, settings, and regression.</p></div><span className="roadmap-state">Planned</span></li>
          </ol>
        </section>
        <footer className="page-footer"><span><Activity size={14} /> Signal Bot frontend migration</span><span>Preview build · no production data</span></footer>
      </main>
    </div>
  </div>;
}
