import { useEffect, useRef, useState } from "react";
import { SystemStatusPanel } from "./SystemStatusPanel";
import { ScreenerPanel } from "./ScreenerPanel";
import { ScreenerControlsPanel } from "./ScreenerControlsPanel";
import { PaperTradingPanel } from "./PaperTradingPanel";
import { StrategyLibraryPanel } from "./StrategyLibraryPanel";
import { WalletTrackingPanel } from "./WalletTrackingPanel";
import { BacktestStudioPanel } from "./BacktestStudioPanel";
import { MLStudioPanel } from "./MLStudioPanel";
import { ResearchPanel } from "./ResearchPanel";
import { NewsAIPanel } from "./NewsAIPanel";
import { MasterDataPanel } from "./MasterDataPanel";
import { SettingsPanel } from "./SettingsPanel";
import { Activity, BarChart3, BookOpen, BrainCircuit, Database, ChevronDown, LayoutDashboard, LogOut, Menu, Moon, Settings2, ShieldCheck, Sun, WalletCards, X } from "lucide-react";
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
  { label: "Research", description: "Historical RetailBot2 positions and performance", icon: Activity },
  { label: "News AI", description: "Sentiment signals and macro events", icon: Activity },
  { label: "Master Data", description: "Historical candle and feature coverage", icon: Database },
  { label: "Settings", description: "Preferences and access", icon: Settings2 },
] as const;

const VIEW_METADATA: Record<string, { eyebrow: string; description: string }> = {
  Overview: {
    eyebrow: "SIGNAL BOT / OPERATIONS",
    description: "Real-time engine status, collector health, and database metrics.",
  },
  "Market Screener": {
    eyebrow: "MARKET INTELLIGENCE / SCREENER V2",
    description: "Binance spot and futures signal screener with role-gated detection parameters.",
  },
  "Paper Trading": {
    eyebrow: "EXECUTION / SIMULATION",
    description: "Simulated orders, active positions, realized PnL ledger, and risk circuit breakers.",
  },
  "Strategy Library": {
    eyebrow: "STRATEGIES / QUANT CATALOG",
    description: "Quantitative trading strategies, indicator parameter rules, and AI strategy generation.",
  },
  "Wallet Tracking": {
    eyebrow: "ON-CHAIN / SMART MONEY",
    description: "Whale wallet tracking, token flow analysis, and large transaction alert monitor.",
  },
  "Backtest Studio": {
    eyebrow: "HISTORICAL ANALYSIS / QUANT LAB",
    description: "Multi-factor strategy backtests, walk-forward efficiency analysis, and trade log audit.",
  },
  "ML Studio": {
    eyebrow: "MACHINE LEARNING / AI ENGINE",
    description: "Predictive model tournaments, feature drift PSI monitoring, and automated retraining loops.",
  },
  Research: {
    eyebrow: "RESEARCH / BOT LAB",
    description: "Historical RetailBot2 Shadow and Inverse results. Use Strategy Library and Paper Trading for new simulations.",
  },
  "News AI": {
    eyebrow: "SENTIMENT / MACRO AI",
    description: "Automated crypto news sentiment scoring, macroeconomic events, and impact filtering.",
  },
  "Master Data": {
    eyebrow: "DATA PIPELINE / CANDLES",
    description: "Binance historical candle store coverage, feature engineering pipelines, and collector jobs.",
  },
  Settings: {
    eyebrow: "WORKSPACE / PREFERENCES",
    description: "System theme, authenticated session security, and Telegram notification test controls.",
  },
};

function readInitialTheme(): Theme {
  try {
    const stored = window.localStorage.getItem("signal-bot-ui-theme");
    if (stored === "dark" || stored === "light") return stored;
  } catch { /* Storage may be unavailable in restricted browsing contexts. */ }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

const viewPath = (label: string) => "/modern/" + label.toLowerCase().replaceAll(" ", "-");
function viewFromLocation() {
  return Object.keys(VIEW_METADATA).find(label => viewPath(label) === window.location.pathname) ?? "Overview";
}

export function App() {
  const [theme, setTheme] = useState<Theme>(readInitialTheme);
  const [activeView, setActiveView] = useState<string>(viewFromLocation);
  useEffect(() => {
    const restore = () => setActiveView(viewFromLocation());
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  const selectView = (label: string) => {
    if (activeView !== label) window.history.pushState(null, "", viewPath(label));
    setActiveView(label);
  };
  const [pendingBacktestStrategyId, setPendingBacktestStrategyId] = useState<string | null>(null);
  const [pendingPaperStrategyId, setPendingPaperStrategyId] = useState<string | null>(null);
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
  const handleSelectStrategyForBacktest = (strategyId: string) => { setPendingBacktestStrategyId(strategyId); selectView("Backtest Studio"); setSidebarOpen(false); };
  const handleSelectStrategyForPaper = (strategyId: string) => {
    setPendingPaperStrategyId(strategyId);
    selectView("Paper Trading");
    setSidebarOpen(false);
  };
  const clearPendingBacktestStrategy = () => setPendingBacktestStrategyId(null);

  const viewMeta = VIEW_METADATA[activeView] ?? {
    eyebrow: "WORKSPACE",
    description: "Quantitative trading platform operations and intelligence.",
  };

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
          onClick={() => { selectView(label); setSidebarOpen(false); }}>
          <Icon size={18} aria-hidden="true" /><span>{label}</span>
          {label === "Market Screener" && <span className="nav-count">V2</span>}
        </button>)}
      </nav>
      <div className="sidebar-bottom">
        <div className="security-note"><ShieldCheck size={16} /><span>Server-controlled permissions</span></div>
        <div className="user-profile"><div className="avatar" aria-hidden="true">SB</div>
          <div className="user-meta"><strong>{auth?.user.username ?? "Admin"}</strong><span>{auth?.user.role ?? "Trader"}</span></div>{auth?.user ? <button className="icon-button" aria-label="Sign out" title="Sign out" onClick={() => void auth.logout()}><LogOut size={15} /></button> : <ChevronDown size={15} aria-hidden="true" />}
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
          <div className="system-live-pill" title="Connected to FastAPI backend and MariaDB database">
            <span className="status-dot-live" />
            <span>Connected</span>
          </div>
          <span className="topbar-divider" />
          <button className="icon-button theme-toggle" onClick={toggleTheme} aria-label={"Switch to " + (theme === "dark" ? "light" : "dark") + " theme"} title="Switch theme">
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </div>
      </header>

      <main id="main-content" className="main-content" tabIndex={-1}>
        <div className="page-heading">
          <div>
            <div className="eyebrow">{viewMeta.eyebrow}</div>
            <h1>{activeView}</h1>
            <p className="page-description">{viewMeta.description}</p>
          </div>
        </div>

        {activeView === "Overview" ? (
          <SystemStatusPanel />
        ) : activeView === "Market Screener" ? (
          <><ScreenerPanel /><ScreenerControlsPanel /></>
        ) : activeView === "Paper Trading" ? (
          <PaperTradingPanel initialStrategyId={pendingPaperStrategyId} onInitialStrategyApplied={() => setPendingPaperStrategyId(null)} />
        ) : activeView === "Strategy Library" ? (
          <StrategyLibraryPanel onSelectForBacktest={handleSelectStrategyForBacktest} onSelectForPaper={handleSelectStrategyForPaper} />
        ) : activeView === "Wallet Tracking" ? (
          <WalletTrackingPanel />
        ) : activeView === "Backtest Studio" ? (
          <BacktestStudioPanel initialStrategyId={pendingBacktestStrategyId} onInitialStrategyApplied={clearPendingBacktestStrategy} />
        ) : activeView === "ML Studio" ? (
          <MLStudioPanel />
        ) : activeView === "Research" ? (
          <ResearchPanel historyOnly />
        ) : activeView === "News AI" ? (
          <NewsAIPanel />
        ) : activeView === "Master Data" ? (
          <MasterDataPanel />
        ) : activeView === "Settings" ? (
          <SettingsPanel theme={theme} onToggleTheme={toggleTheme} username={auth?.user.username} role={auth?.user.role} />
        ) : null}

        <footer className="page-footer">
          <span><Activity size={14} /> Signal Bot Quantitative Platform</span>
          <span>FastAPI Engine · Binance Market Data · MariaDB</span>
        </footer>
      </main>
    </div>
  </div>;
}
