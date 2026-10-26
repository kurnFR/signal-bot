# Signal Bot frontend migration (preview only)

This folder contains an isolated React + TypeScript foundation for the UIX migration. It is not mounted by FastAPI and does not replace web/static/index.html. The existing dashboard remains the default UI.

## Requirements
- Node.js 20 or newer
- npm 10 or newer

## Commands
Run from this directory:
- npm install --no-audit --no-fund — install exact versions declared in package.json.
- npm run dev — start the Vite preview server.
- npm run typecheck — run strict TypeScript checks.
- npm test — run component tests in jsdom.
- npm run build — type-check and create an isolated production build in dist/.

Dependency versions are pinned exactly. A committed npm lockfile is required before this foundation is accepted for feature migration; do not use this preview as a production deployment artifact.

## Safety boundary
The preview uses mock presentation state only. It does not call the backend, perform paper/live trades, start workers, alter settings, or send Telegram messages. Domain API integration must be added in later reviewed slices with typed contracts and parity tests.