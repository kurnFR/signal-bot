import { useEffect, useState } from "react";
import { Command } from "cmdk";
import {
  Activity,
  Search,
  Settings,
  ShieldAlert,
} from "lucide-react";
import { WORKSPACE_GROUPS } from "../lib/workspaceNavigation";
import { useProTraderStore } from "../lib/store";

interface CommandPaletteProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onSelectView: (view: string) => void;
  onSelectSymbol?: (symbol: string) => void;
  onEmergencyStop?: () => void;
}

const COMMON_SYMBOLS = [
  "BTCUSDT",
  "ETHUSDT",
  "SOLUSDT",
  "BNBUSDT",
  "DOGEUSDT",
  "XRPUSDT",
  "ADAUSDT",
  "AVAXUSDT",
  "LINKUSDT",
  "NEARUSDT",
];

export function CommandPalette({
  open: controlledOpen,
  onOpenChange,
  onSelectView,
  onSelectSymbol,
  onEmergencyStop,
}: CommandPaletteProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const isOpen = controlledOpen !== undefined ? controlledOpen : internalOpen;
  const setOpen = (val: boolean) => {
    if (onOpenChange) onOpenChange(val);
    else setInternalOpen(val);
  };
  const setSelectedSymbol = useProTraderStore((s) => s.setSelectedSymbol);

  // Toggle on Cmd+K or Ctrl+K
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen(!isOpen);
      }
    };

    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, [isOpen]);

  const handleSelectSymbol = (symbol: string) => {
    setSelectedSymbol(symbol);
    onSelectSymbol?.(symbol);
    setOpen(false);
  };

  const handleNavigate = (view: string) => {
    onSelectView(view);
    setOpen(false);
  };

  return (
    <>
      {controlledOpen === undefined && <button
        type="button"
        className="button button-secondary button-sm"
        onClick={() => setOpen(true)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          padding: "4px 10px",
          fontSize: "12px",
          color: "var(--color-muted)",
        }}
        title="Open command palette (Cmd+K / Ctrl+K)"
      >
        <Search size={14} />
        <span>Quick Search…</span>
        <kbd
          style={{
            fontSize: "10px",
            padding: "1px 4px",
            backgroundColor: "rgba(255, 255, 255, 0.1)",
            borderRadius: "4px",
            border: "1px solid var(--color-border)",
          }}
        >
          ⌘K
        </kbd>
      </button>}

      {isOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            backgroundColor: "rgba(0, 0, 0, 0.7)",
            backdropFilter: "blur(4px)",
            zIndex: 99999,
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "center",
            paddingTop: "15vh",
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) setOpen(false);
          }}
        >
          <div
            style={{
              width: "100%",
              maxWidth: "580px",
              backgroundColor: "var(--color-surface, #1e293b)",
              borderRadius: "12px",
              border: "1px solid var(--color-border, #334155)",
              boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.6)",
              overflow: "hidden",
            }}
          >
            <Command
              label="Command Menu"
              style={{
                display: "flex",
                flexDirection: "column",
                width: "100%",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  padding: "12px 16px",
                  borderBottom: "1px solid var(--color-border, #334155)",
                  gap: "10px",
                }}
              >
                <Search size={18} style={{ color: "var(--color-muted)" }} />
                <Command.Input
                  placeholder="Type a symbol, workstation view, or action…"
                  autoFocus
                  style={{
                    flex: 1,
                    backgroundColor: "transparent",
                    border: "none",
                    outline: "none",
                    color: "var(--color-text)",
                    fontSize: "14px",
                  }}
                />
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  style={{
                    backgroundColor: "transparent",
                    border: "none",
                    color: "var(--color-muted)",
                    cursor: "pointer",
                    fontSize: "11px",
                  }}
                >
                  ESC
                </button>
              </div>

              <Command.List
                style={{
                  maxHeight: "360px",
                  overflowY: "auto",
                  padding: "8px",
                }}
              >
                <Command.Empty
                  style={{
                    padding: "24px",
                    textAlign: "center",
                    color: "var(--color-muted)",
                    fontSize: "13px",
                  }}
                >
                  No matching symbol or action found.
                </Command.Empty>

                {WORKSPACE_GROUPS.map(({ label: group, items }) => (
                  <Command.Group key={group} heading={group} style={{ padding: "4px 8px", fontSize: "11px", color: "var(--color-muted)" }}>
                    {items.map(({ label, icon: Icon, keywords }) => (
                      <Command.Item key={label} value={label} keywords={keywords} onSelect={() => handleNavigate(label)}
                        style={{ display: "flex", alignItems: "center", gap: "10px", padding: "8px 10px", borderRadius: "6px", cursor: "pointer", fontSize: "13px" }}>
                        <Icon size={15} aria-hidden="true" /> {label}
                      </Command.Item>
                    ))}
                  </Command.Group>
                ))}

                <Command.Group heading="Active Symbols" style={{ padding: "4px 8px", fontSize: "11px", color: "var(--color-muted)" }}>
                  {COMMON_SYMBOLS.map((sym) => (
                    <Command.Item
                      key={sym}
                      value={sym}
                      onSelect={() => handleSelectSymbol(sym)}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "8px 10px",
                        borderRadius: "6px",
                        cursor: "pointer",
                        fontSize: "13px",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <Activity size={14} style={{ color: "var(--color-accent, #3b82f6)" }} />
                        <strong>{sym}</strong>
                      </div>
                      <span className="notice-tag" style={{ fontSize: "10px" }}>FUTURES PERP</span>
                    </Command.Item>
                  ))}
                </Command.Group>

                <Command.Group heading="Help & Setup" style={{ padding: "4px 8px", fontSize: "11px", color: "var(--color-muted)" }}>
                  <Command.Item
                    onSelect={() => {
                      setOpen(false);
                      window.dispatchEvent(new CustomEvent("signal-bot:open-onboarding"));
                    }}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "10px",
                      padding: "8px 10px",
                      borderRadius: "6px",
                      cursor: "pointer",
                      fontSize: "13px",
                    }}
                  >
                    <Settings size={15} /> First-Run Setup & Onboarding Wizard
                  </Command.Item>
                </Command.Group>

                {onEmergencyStop && (
                  <Command.Group heading="Safety & Circuit Breaker" style={{ padding: "4px 8px", fontSize: "11px", color: "var(--color-muted)" }}>
                    <Command.Item
                      onSelect={() => {
                        setOpen(false);
                        onEmergencyStop();
                      }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "10px",
                        padding: "8px 10px",
                        borderRadius: "6px",
                        cursor: "pointer",
                        fontSize: "13px",
                        color: "var(--color-negative, #ef4444)",
                      }}
                    >
                      <ShieldAlert size={15} /> Trigger Emergency Stop Circuit Breaker
                    </Command.Item>
                  </Command.Group>
                )}
              </Command.List>
            </Command>
          </div>
        </div>
      )}
    </>
  );
}
