# Signal Bot dashboard

FastAPI serves the built React dashboard at `/modern/`. Each workspace view has a URL, including `/modern/paper-trading`. Build the frontend before starting the web process. The legacy dashboard is the default at `/`. Set `SIGNAL_BOT_USE_MODERN_UI=1` and restart the web process after staging acceptance to serve the modern dashboard at the root. Set the flag to 0 and restart to restore the legacy root.

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

Dependency versions are pinned in `package.json` and the committed `package-lock.json`. CI uses `npm ci` so the install is reproducible.
