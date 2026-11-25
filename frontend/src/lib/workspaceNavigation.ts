import { Activity, BarChart3, BookOpen, BrainCircuit, Database, Flame, LayoutDashboard, Newspaper, Settings2, Wallet, WalletCards, Zap, type LucideIcon } from "lucide-react";

export const WORKSPACE_GROUPS: { label: string; items: { label: string; icon: LucideIcon; keywords?: string[] }[] }[] = [
  {
    label: "Prepare workspace",
    items: [
      { label: "Overview", icon: LayoutDashboard },
      { label: "Master Data", icon: Database },
    ],
  },
  {
    label: "Research markets",
    items: [
      { label: "Trading Desk", icon: Zap },
      { label: "Market Screener", icon: Activity },
      { label: "Liquidation Heatmap", icon: Flame, keywords: ["futures", "liquidations", "leverage"] },
      { label: "Wallet Tracking", icon: Wallet },
      { label: "News AI", icon: Newspaper },
      { label: "Research", icon: Activity },
    ],
  },
  {
    label: "Validate strategy",
    items: [
      { label: "Strategy Library", icon: BookOpen },
      { label: "Backtest Studio", icon: BarChart3 },
      { label: "ML Studio", icon: BrainCircuit },
    ],
  },
  {
    label: "Simulation",
    items: [
      { label: "Paper Trading", icon: WalletCards },
    ],
  },
  {
    label: "Workspace settings",
    items: [{ label: "Settings", icon: Settings2, keywords: ["Telegram", "alerts"] }],
  },
];

export const viewPath = (label: string) => "/modern/" + label.toLowerCase().replaceAll(" ", "-");
