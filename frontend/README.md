# Signal Bot frontend migration (preview only)

This folder contains an isolated React + TypeScript foundation for the UIX migration. It is **not mounted by FastAPI** and does not replace `web/static/index.html`. The existing dashboard remains the default UI.

## Requirements
- Node.js 20 or newer
- npm 10 or newer

## Commands
Run from this directory:
- `npm ci --no-audit --no-fund` — install the dependency tree recorded in `package-lock.json`.
- `npm run dev` — start the Vite preview server.
- `npm run typecheck` — run strict TypeScript checks.
- `npm test` — run component tests in jsdom.
- `npm run build` — type-check and create an isolated production build in `dist/`.

Dependency versions are pinned in `package.json` and the committed `package-lock.json`. CI uses `npm ci` so the install is reproducible. Do not use this preview as a production deployment artifact.

## Current integration and safety boundary

The preview is **mostly read-only, with four explicit mutation surfaces**. First, the Market Screener editor can save `/api/research/screener/control` settings for `admin` and `trader` roles; this changes screener configuration but does not send a Telegram message or restart the worker. Second, the Master Data panel lets `admin` and `trader` roles submit a backfill or feature-build job after reviewing a confirmation dialog; these jobs can write OHLCV/features and start background workers. Third, Backtest Studio lets `admin` and `trader` roles run a simulation after reviewing and confirming the configuration; the request asks the backend to persist results, but it does not execute live trades. The UI explicitly warns when `runId` is null so a completed simulation is not mistaken for a confirmed save. Fourth, Strategy Library lets `admin` and `trader` roles save a custom strategy definition and delete an existing definition; deletion requires confirmation, and creating/deleting does not activate or promote the strategy. Strategy Library also exposes two explicit compute actions: **Quick test** runs a non-persisted simulation over up to 1,000 stored candles; **Generate draft with AI** sends the prompt to the configured backend generator, which may call an external provider and incur usage costs. Generated output remains a draft until the user reviews it and explicitly saves it. Viewer sessions cannot access these actions, and backend authorization remains authoritative. Do not use these controls against a production-connected API unless you intend to change screener configuration, start data-processing jobs, run and persist a backtest, change the saved strategy library, or spend compute/provider usage on a strategy test or AI draft. Other panels remain read-only; the preview does not expose strategy promotion, deployment, wallet-tracking controls, or Telegram-send actions.

The React preview remains isolated from FastAPI production serving until route/API parity, permission behavior, responsive layouts, accessibility, and browser-level regression gates are complete and explicitly accepted. Component tests in jsdom do not replace Playwright or real-browser/device acceptance.
