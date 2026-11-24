# Signal Bot Modern UI

React 19 + TypeScript + Vite workstation served by FastAPI.

## Serving & Routing

- **Default Root UI:** FastAPI serves the built Modern UI (`frontend/dist/index.html`) at `/` and `/modern/*` automatically whenever `frontend/dist/index.html` exists. Set `SIGNAL_BOT_USE_MODERN_UI=0` and restart to fall back to the legacy static dashboard (`web/static/index.html`).
- **Workspace Views (12 routes):** `/modern/overview`, `/modern/trading-desk`, `/modern/market-screener`, `/modern/paper-trading`, `/modern/strategy-library`, `/modern/wallet-tracking`, `/modern/backtest-studio`, `/modern/ml-studio`, `/modern/research`, `/modern/news-ai`, `/modern/master-data`, and `/modern/settings`.
- **Real-Time & Charting:** Uses TradingView `lightweight-charts` v5 (candlestick OHLCV + Entry/SL/TP/Mark lines + synchronized Equity/Drawdown curves), `react-resizable-panels` (multi-pane Trading Desk), `cmdk` (`Cmd+K` / `Ctrl+K` Command Palette), `zustand`, and authenticated WebSocket channels (`/ws/market`, `/ws/paper`, `/ws/system`) using single-use tickets from `POST /api/auth/ws-ticket`.

## Requirements

- Node.js 20 or newer (CI uses Node 22)
- npm 10 or newer

## Commands

Run from `frontend/`:

- `npm ci --no-audit --no-fund` — install pinned dependencies from `package-lock.json`.
- `npm run dev` — start the Vite dev server on `http://127.0.0.1:5173` (proxies `/api` and `/ws` with WebSocket upgrade to `http://127.0.0.1:8050`).
- `npm run typecheck` — run strict TypeScript checking (`tsc --noEmit`).
- `npm test` — run Vitest unit and component tests in jsdom.
- `npm run build` — typecheck and build the production bundle in `dist/` (also executed automatically in Stage 1 of the root `Dockerfile`).
