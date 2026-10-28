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

The preview is **mostly read-only, with two explicit mutation surfaces**. First, the Market Screener editor can save `/api/research/screener/control` settings for `admin` and `trader` roles. This changes screener configuration (including the Telegram alert gate and detection thresholds); it does not send a Telegram message or restart the worker. Second, the Master Data panel lets `admin` and `trader` roles submit a backfill or feature-build job after reviewing a confirmation dialog; these jobs can write OHLCV/features and start background workers. Viewer sessions cannot access either editor, and backend authorization remains authoritative. Do not use either control surface against a production-connected API unless you intend to change the live configuration or start data-processing jobs. Other panels remain read-only; the preview does not expose trade execution, strategy promotion, deployment, wallet-tracking controls, or Telegram-send actions.

The React preview remains isolated from FastAPI production serving until route/API parity, permission behavior, responsive layouts, accessibility, and browser-level regression gates are complete and explicitly accepted. Component tests in jsdom do not replace Playwright or real-browser/device acceptance.
