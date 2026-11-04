# Signal Bot — Modern UI Reversible Cutover Plan

**Repository:** `kurnFR/signal-bot`  
**Target:** Controlled, reversible cutover from the legacy static dashboard (`web/static/`) to the modern React + TypeScript dashboard (`frontend/dist/`) requiring a web service reload/restart.  
**Author:** Antigravity  
**Status:** Prepared for Owner Approval (Cutover switch remains disabled until authorized)  
**Reference:** [`docs/ANTIGRAVITY_MODERN_UI_MIGRATION_PLAN.md`](ANTIGRAVITY_MODERN_UI_MIGRATION_PLAN.md), Phase 9  

---

## 1. Overview & Serving Architecture

The Signal Bot FastAPI backend supports both the legacy static frontend and the modern React application through a reversible environment toggle.

> [!IMPORTANT]
> **Process Restart Requirement:** Toggling `SIGNAL_BOT_USE_MODERN_UI` requires reloading or restarting the web process (`systemctl reload-or-restart` or supervisor reload) for the environment change to take effect. If not fronted by an upstream reverse proxy with socket handover (e.g., Nginx), a brief interruption to in-flight HTTP connections may occur.
>
> **Data Compatibility:** Both frontends communicate with the exact same FastAPI endpoints and MariaDB database tables. No schema mutations or destructive conversions occur during cutover or rollback. However, operations should be performed during low-traffic windows after verifying that paper trading loops and background collectors are stable.

### Current Serving Mechanism in `web/app.py`
```python
FRONTEND_DIST_DIR = os.path.join(BASE_DIR, "frontend", "dist")
if os.path.exists(FRONTEND_DIST_DIR):
    app.mount("/modern", StaticFiles(directory=FRONTEND_DIST_DIR, html=True), name="modern")
    assets_dir = os.path.join(FRONTEND_DIST_DIR, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend_assets")

@app.get("/")
def serve_index():
    if os.getenv("SIGNAL_BOT_USE_MODERN_UI", "0").lower() in ("1", "true", "yes"):
        modern_index = os.path.join(FRONTEND_DIST_DIR, "index.html")
        if os.path.exists(modern_index):
            return FileResponse(modern_index)

    index_path = os.path.join(STATIC_DIR, "index.html")
    ...
```

- When `SIGNAL_BOT_USE_MODERN_UI=0` (Default): `GET /` serves the legacy static dashboard from `web/static/index.html`. The modern UI remains accessible at `/modern` for preview and verification.
- When `SIGNAL_BOT_USE_MODERN_UI=1`: `GET /` immediately serves the compiled React application from `frontend/dist/index.html`.

---

## 2. Pre-Cutover Build & Artifact Generation

Before enabling the cutover toggle in production, ensure that the modern frontend bundle is built deterministically:

1. **Clean Installation:**
   ```bash
   cd frontend
   npm ci --no-audit --no-fund
   ```

2. **Static Typecheck:**
   ```bash
   npm run typecheck
   ```

3. **Production Bundle Compilation:**
   ```bash
   npm run build
   ```
   *Expected Output:*
   - `frontend/dist/index.html`
   - `frontend/dist/assets/index-[hash].css`
   - `frontend/dist/assets/index-[hash].js`

4. **Verify Artifact Existence:**
   ```bash
   test -f frontend/dist/index.html && echo "Dist artifact present"
   ```

---

## 3. Static Asset Serving & Cache Control Headers

- **Content-Hashed Bundles:**
  Vite compiles assets with content hashes in the filename (e.g., `index-BCp-CrUn.js`). These assets can be cached aggressively by browsers and CDNs:
  ```http
  Cache-Control: public, max-age=31536000, immutable
  ```
- **HTML Entrypoint (`index.html`):**
  The entrypoint HTML document must never be aggressively cached to allow instant rollbacks and script updates:
  ```http
  Cache-Control: no-cache, no-store, must-revalidate
  Pragma: no-cache
  Expires: 0
  ```

---

## 4. API Route Precedence & Route Fallback

- **Route Precedence:**
  In FastAPI, all router definitions for `/api/*` are registered **before** static mounts and the catch-all `GET /` route:
  1. `/api/auth/*`
  2. `/api/paper/*`
  3. `/api/search/*`
  4. `/api/master-data/*`
  5. `/api/backtest/*`
  6. `/api/settings/*`
  7. `/api/system/*`
  8. `/api/custom-strategy/*`
  9. `/api/telegram/*`
  10. `/api/ml/*`
  11. `/api/news/*`
  12. `/api/research/*`
  13. `/api/wallet/*`
  14. `/static/*` (Legacy static assets)
  15. `/assets/*` (Modern Vite compiled assets)
  16. `/modern` (Modern UI preview mount)
  17. `GET /` (Root dashboard handler)

- **Route Safety:**
  Because `/api/*` routes are mounted with explicit path prefixes before the index file response, no API endpoint can be masked or intercepted by the frontend.

---

## 5. Authentication & Session Preservation

- **Storage Compatibility:**
  Both the legacy dashboard and the modern React application use `localStorage.getItem("token")` to store JWT tokens.
- **Seamless Transition:**
  Existing authenticated sessions do not require re-login upon cutover. When the browser loads the modern UI at `/`, it reads the existing token from `localStorage` and validates it against `GET /api/auth/me`.
- **Forced Password Change:**
  If a user has `must_change_password=True`, the modern `AuthGate` component intercepts navigation and presents the password update dialog, preventing access until completed.

---

## 6. Step-by-Step Cutover Procedure

1. **Step 1: Build the latest production bundle**
   ```bash
   cd /home/BIS/signal-bot/frontend
   npm run build
   ```

2. **Step 2: Smoke test the `/modern` preview route**
   - Access `http://<server-ip>:8050/modern` in a browser.
   - Sign in as `viewer`, `trader`, and `admin`.
   - Verify all 11 tabs load live data properly.

3. **Step 3: Enable the modern UI serving switch**
   Update the system environment or `.env` file:
   ```bash
   # In .env or systemd service environment
   SIGNAL_BOT_USE_MODERN_UI=1
   ```

4. **Step 4: Reload the web server**
   ```bash
   # If running via systemd:
   systemctl reload-or-restart signal-bot-web
   # Or if running via run_all.py:
   # Restart the web_server sub-process
   ```

5. **Step 5: Post-cutover validation**
   Execute HTTP smoke checks:
   ```bash
   curl -I http://127.0.0.1:8050/
   # Should return HTTP 200 with HTML content referencing /assets/index-*.js
   ```

---

## 7. Immediate Rollback Procedure (< 60 Seconds)

If any unexpected regression, rendering defect, or user disruption occurs, the rollback procedure is immediate and non-destructive:

1. **Disable the modern UI toggle:**
   ```bash
   # In .env or environment:
   SIGNAL_BOT_USE_MODERN_UI=0
   ```

2. **Reload the web server:**
   ```bash
   systemctl reload-or-restart signal-bot-web
   ```

3. **Verify Legacy Dashboard Restored:**
   ```bash
   curl -s http://127.0.0.1:8050/ | grep -q "dashboard_enhancements.js" && echo "Rollback successful: legacy dashboard active"
   ```

*Note: Database tables and stored data are shared directly between both interfaces without interface-specific schema mutations; switching serving modes does not modify persistent database records. Operators should still verify service health and backfill job status before and after reloading.*

---

## 8. Post-Cutover Verification Checklist

- [ ] `GET /` returns React application HTML (`<div id="root"></div>`).
- [ ] Static assets (`/assets/index-*.js`, `/assets/index-*.css`) load with HTTP 200.
- [ ] User login and existing token restoration succeed.
- [ ] Dark and Light theme toggle persists across page reloads.
- [ ] All 11 navigation views render without JavaScript console exceptions.
- [ ] Live WebSocket or polling data refreshes without duplicate queries.
- [ ] Trader / Admin role controls function with confirmation prompts.
- [ ] Viewer accounts are restricted to read-only views.
