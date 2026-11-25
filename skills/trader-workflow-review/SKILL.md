---
name: trader-workflow-review
description: Review or change Signal Bot's trader dashboard handoffs from market research and saved validation evidence to Telegram and paper execution.
---

Trace both dashboard entry points. The modern React shell is in `frontend/src/App.tsx` and its sidebar and command palette share `frontend/src/lib/workspaceNavigation.ts`. The legacy shell is in `web/static/index.html`.

- Follow market data and research into Strategy Library, then a saved Backtest Studio run or eligible ML Studio artifact, then Telegram preferences and paper execution. Preserve the selected evidence when navigating between views. Use a supported paper timeframe and carry the tested strategy parameters and model identifier into the appropriate paper endpoint.
- Check activation separately from monitoring and risk reduction. Review new deployments, single and batch resumes, and benchmark auto-promotion. Keep position and order monitoring, pausing, and emergency controls available during preparation.
- Single-strategy backtests produce research evidence. The multi-strategy benchmark can promote through the saved policy. Verify that a modern workspace research benchmark sends `allow_auto_promotion: false` and that a promotion-capable run requires explicit consent.
- Review opt-in Telegram behavior independently for paper, screener, and wallet alerts. Surface partial evaluation and delivery outcomes accurately. Test external side effects with mocked API responses unless an authorized live test is requested.
- Use `npm --prefix frontend test`, `npm --prefix frontend run build`, and the relevant Playwright and Python route tests. Verify navigation at desktop and narrow widths.
