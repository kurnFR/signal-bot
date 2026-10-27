import { useEffect, useRef, useState } from "react";
import { SystemStatusPanel } from "./SystemStatusPanel";
import { ScreenerPanel } from "./ScreenerPanel";
import { PaperTradingPanel } from "./PaperTradingPanel";
import { StrategyLibraryPanel } from "./StrategyLibraryPanel";
import { WalletTrackingPanel } from "./WalletTrackingPanel";
import { BacktestStudioPanel } from "./BacktestStudioPanel";
import { MLStudioPanel } from "./MLStudioPanel";
import { ResearchPanel } from "./ResearchPanel";
import { NewsAIPanel } from "./NewsAIPanel";
import { MasterDataPanel } from "./MasterDataPanel";
import { SettingsPanel } from "./SettingsPanel";
import { Activity, BarChart3, Bell, BookOpen, BrainCircuit, Database, ChevronDown, CircleHelp, Command, LayoutDashboard, LogOut, Menu, Moon, Search, Settings2, ShieldCheck, Sun, WalletCards, X } from "lucide-react";
import { useAuth } from "./AuthGate";

type Theme = "dark" | "light";
const NAV_ITEMS = [
  { label: "Overview", description: "System overview", icon: LayoutDashboard },
  { label: "Market Screener", description: "Signals and market coverage", icon: Activity },
  { label: "Paper Trading", description: "Simulated orders and positions", icon: WalletCards },
  { label: "Strategy Library", description: "Saved rules and indicator catalog", icon: BookOpen },
  { label: "Wallet Tracking", description: "On-chain wallet and token flow monitor", icon: WalletCards },
  { label: "Backtest Studio", description: "Saved backtest results and trade evidence", icon: BarChart3 },
  { label: "ML Studio", description: "Saved ML experiments and model artifacts", icon: BrainCircuit },
  { label: "Research", description: "RetailBot2 positions and strategy performance", icon: Activity },
  { label: "News AI", description: "Sentiment signals and macro events", icon: Activity },
  { label: "Master Data", description: "Historical candle and feature coverage", icon: Database },
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
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const sidebarCloseButtonRef = useRef<HTMLButtonElement>(null);
  const wasSidebarOpen = useRef(false);
  const auth = useAuth();

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    try { window.localStorage.setItem("signal-bot-ui-theme", theme); } catch { /* Current-session theme still applies. */ }
  }, [theme]);

  useEffect(() => {
    if (!sidebarOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [sidebarOpen]);

  useEffect(() => {
    if (sidebarOpen) {
      wasSidebarOpen.current = true;
      sidebarCloseButtonRef.current?.focus();
      return;
    }
    if (wasSidebarOpen.current) {
      wasSidebarOpen.current = false;
      menuButtonRef.current?.focus();
    }
  }, [sidebarOpen]);

  const toggleTheme = () => setTheme((current) => current === "dark" ? "light" : "dark");

  return <div className="app-frame">
    <a className="skip-link" href="#main-content">Skip to main content</a>
    {sidebarOpen && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
    <aside id="dashboard-sidebar" className={"sidebar " + (sidebarOpen ? "sidebar-open" : "")} aria-label="Dashboard sidebar">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true"><Activity size={20} strokeWidth={2.5} /></div>
        <div className="brand-copy"><strong>Signal Bot</strong><span>QUANTITATIVE PLATFORM</span></div>
        <button ref={sidebarCloseButtonRef} className="icon-button sidebar-close" aria-label="Close navigation" onClick={() => setSidebarOpen(false)}><X size={18} /></button>
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
          <div className="user-meta"><strong>{auth?.user.username ?? "Preview workspace"}</strong><span>{auth?.user.role ?? "Frontend preview"}</span></div>{auth?.user ? <button className="icon-button" aria-label="Sign out" title="Sign out" onClick={() => void auth.logout()}><LogOut size={15} /></button> : <ChevronDown size={15} aria-hidden="true" />}
        </div>
      </div>
    </aside>

    <div className="main-column">
      <header className="topbar">
        <div className="topbar-left">
          <button ref={menuButtonRef} className="icon-button mobile-menu" aria-label="Open navigation" aria-controls="dashboard-sidebar" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(true)}><Menu size={20} /></button>
          <div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-separator">/</span><strong>{activeView}</strong></div>
        </div>
        <div className="topbar-actions">
          <div className="preview-status"><span className="status-dot" /> UI preview</div>
          <button className="icon-button" aria-label="Search (preview only)" title="Search is not available in this preview" disabled><Search size={18} /></button>
          <button className="icon-button" aria-label="Notifications (preview only)" title="Notifications are not available in this preview" disabled><Bell size={18} /></button>
          <span className="topbar-divider" />
          <button className="icon-button theme-toggle" onClick={toggleTheme} aria-label={"Switch to " + (theme === "dark" ? "light" : "dark") + " theme"} title="Switch theme">
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <button className="help-button" aria-label="Help (preview only)" title="Help is not available in this preview" disabled><CircleHelp size={17} /><span>Help</span></button>
        </div>
      </header>

      <main id="main-content" className="main-content" tabIndex={-1}>
        <div className="page-heading"><div>
          <div className="eyebrow">SIGNAL BOT / FRONTEND FOUNDATION</div><h1>{activeView}</h1>
          <p className="page-description">A component-based interface is being built alongside the existing dashboard.</p>
        </div><div className="heading-actions"><button className="button button-secondary" onClick={() => setActiveView("Overview")}><Command size={16} /> Preview shell</button></div></div>

        <section className="migration-notice" aria-labelledby="preview-notice-title">
          <div className="notice-icon"><ShieldCheck size={20} /></div>
          <div><h2 id="preview-notice-title">Incremental frontend migration</h2><p>Overview and Market Screener V2 are connected to existing APIs. Paper Trading is a read-only ledger and risk monitor. Strategy Library, Wallet Tracking, Backtest Studio, and ML Studio are read-only views. Wallet Tracking displays tracker health, monitored addresses, token flow, and recent events. Backtest Studio displays saved results and closed-trade evidence; ML Studio displays persisted experiments and model eligibility. Research displays RetailBot2 positions, closed trades, and lifecycle readback. News AI displays persisted sentiment metrics, signals, and macro events. Master Data displays stored candle coverage, feature availability, and recent job status. Running backtests or experiments, deploying models, and changing promotion policy are not available. Mutating strategy, wallet tracking, trading, worker, deployment, and Telegram actions remain deliberately unavailable. The legacy dashboard remains the default application.</p></div>
          <span className="notice-tag">READ ONLY</span>
        </section>

        {activeView === "Overview" ? <SystemStatusPanel /> : activeView === "Market Screener" ? <ScreenerPanel /> : activeView === "Paper Trading" ? <PaperTradingPanel /> : activeView === "Strategy Library" ? <StrategyLibraryPanel /> : activeView === "Wallet Tracking" ? <WalletTrackingPanel /> : activeView === "Backtest Studio" ? <BacktestStudioPanel /> : activeView === "ML Studio" ? <MLStudioPanel /> : activeView === "Research" ? <ResearchPanel /> : activeView === "News AI" ? <NewsAIPanel /> : activeView === "Master Data" ? <MasterDataPanel /> : activeView === "Settings" ? <SettingsPanel theme={theme} onToggleTheme={toggleTheme} username={auth?.user.username} role={auth?.user.role} /> : <section className="panel preview-placeholder"><div className="panel-heading"><div><h2>{activeView} migration slice</h2><p>This view remains preview-only until its routes, permissions, schemas, and side effects have been verified.</p></div><span className="panel-counter">NOT CONNECTED</span></div><p className="empty-state">No live data or controls are exposed here yet. Existing dashboard functionality remains available in the legacy application.</p></section>}

        <section className="panel" aria-labelledby="roadmap-title">
          <div className="panel-heading"><div><h2 id="roadmap-title">Migration roadmap</h2><p>Small, testable slices with legacy fallback preserved.</p></div><span className="panel-counter">02 / 05</span></div>
          <ol className="roadmap-list">
            <li className="roadmap-complete"><span className="roadmap-step">00</span><div><strong>Inventory and parity plan</strong><p>Document existing views, endpoints, roles, and consequential side effects.</p></div><span className="roadmap-state">Documentation merged</span></li>
            <li className="roadmap-complete"><span className="roadmap-step">01</span><div><strong>Frontend foundation</strong><p>React, TypeScript, local styling pipeline, accessible shell, and tests.</p></div><span className="roadmap-state">Merged</span></li>
            <li className="roadmap-complete"><span className="roadmap-step">02</span><div><strong>Overview and authentication</strong><p>Existing auth gate, typed system-status reads, loading/error states.</p></div><span className="roadmap-state">Merged · not served</span></li>
            <li className="roadmap-complete"><span className="roadmap-step">03</span><div><strong>Market Screener V2</strong><p>Signals, metrics, search, alert gate, and role-gated detection overrides.</p></div><span className="roadmap-state">Merged · read and control UI</span></li>
            <li className="roadmap-active"><span className="roadmap-step">04</span><div><strong>Domain views and final parity</strong><p>Paper Trading read-only monitor first, then wallets, ML, backtests, research, settings, and parity regression.</p></div><span className="roadmap-state">In progress</span></li>
          </ol>
        </section>
        <footer className="page-footer"><span><Activity size={14} /> Signal Bot frontend migration</span><span>Read-only API preview · legacy dashboard unchanged</span></footer>
      </main>
    </div>
  </div>;
}
