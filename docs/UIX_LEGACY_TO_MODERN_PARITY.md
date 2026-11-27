# Signal Bot Legacy to Modern UI parity

Generate the current control and API matrix from the repository root.

```bash
PYTHONPATH=. python tests/test_frontend_fetch_method_parity.py
```

The report inventories Legacy HTML controls with IDs and lists reviewed Modern equivalents. “None recorded” means the control mapping still needs review. The mutation section lists mounted FastAPI mutation routes, source locations of detected Modern callers, and documented exceptions. The backend test suite fails when a mutation loses its caller or gains a caller while still listed as an exception. The source scan checks literal calls and the shared request helpers declared in the parity check.

## Overview

Modern Overview shows the candle total, dataset count, and distinct symbol count from `GET /api/master-data/coverage`. It lists the ten most recent Backtest Studio runs from `GET /api/backtest/history?limit=10`, using the same values and order as Legacy Home. System and collector health come from the system status API.

The previous workflow audit is available in Git history. Browser acceptance against seeded MariaDB should compare the displayed coverage totals and recent run rows across both UIs.

## Pending owner decisions for research endpoints

- ML reinforcement-learning training and model listing, plus walk-forward analysis, need a usage decision before the Modern UI adds workflows for them. The proposed default is deprecation if the owner no longer uses them.
- The retired RetailBot2 research control can remain available as read-only status in Settings under RetailBot2 Archive, subject to the owner's decision.
