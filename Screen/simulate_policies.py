"""Replay two queue policies over the WO71953 historical period:
A: Freshness fix with 5 shared slots
B: Same fix with at most 4 parent watches/hr and 5th slot reserved for confirmation
"""
import json
from datetime import datetime, timezone
import numpy as np
from Screen import momentum as m
from db.db import get_pool

def run_simulation(include_all_candidates=False):
    split_str = '2026-11-15T21:17:38Z'
    split_ms = m.utc_ms(split_str)
    end_ms = m.utc_ms('2026-11-20T02:05:40Z')

    with open('/tmp/breaks_info.json') as f:
        known_breaks = json.load(f)  # id -> {symbol, watch_start, break_close, mult, direction}

    with get_pool().get_connection() as conn, conn.cursor(dictionary=True) as cur:
        if include_all_candidates:
            cur.execute("""SELECT id, symbol, signal_type, direction, quality_score, rvol, candle_time, detected_at, delivery_status, delivered_at
                           FROM smart_money_signals
                           WHERE candle_time >= %s AND candle_time <= '2026-11-20T02:05:40Z'
                             AND quality_score >= 80 AND direction IN ('BULLISH', 'BEARISH')
                             AND parent_watch_id IS NULL
                           ORDER BY candle_time ASC, detected_at ASC, id ASC""", (split_str,))
        else:
            cur.execute("""SELECT id, symbol, signal_type, direction, quality_score, rvol, candle_time, detected_at, delivery_status, delivered_at
                           FROM smart_money_signals
                           WHERE delivery_status = 'SENT' AND parent_watch_id IS NULL
                             AND delivered_at >= '2026-11-15 21:17:38' AND delivered_at <= '2026-11-20 02:05:40'
                           ORDER BY candle_time ASC, detected_at ASC, id ASC""")
        rows = cur.fetchall()

    print(f"Loaded {len(rows)} candidates (include_all_candidates={include_all_candidates})")

    # Format candidate objects
    candidates = []
    for r in rows:
        start_ms = m.utc_ms(r['candle_time'])
        detect_ms = m.utc_ms(r['detected_at']) if r['detected_at'] else start_ms + m.MINUTE
        close_ms = start_ms + m.MINUTE
        deadline_ms = start_ms + 6 * m.MINUTE  # watch open + 6m = close + 5m
        candidates.append({
            'id': r['id'],
            'symbol': r['symbol'],
            'direction': r['direction'],
            'quality_score': float(r['quality_score'] or 0),
            'rvol': float(r['rvol'] or 0),
            'start_ms': start_ms,
            'close_ms': close_ms,
            'detect_ms': detect_ms,
            'deadline_ms': deadline_ms,
        })

    results = {}
    for policy in ('A', 'B'):
        # Discrete-event simulation
        delivered = []  # list of dict(type='watch'|'child', time_ms, symbol, id, delay_sec, first_close)
        pending_watches = []
        scheduled_children = []  # children not yet arrived at break_close
        pending_children = []    # children arrived at break_close, awaiting delivery
        displaced_watches = set()
        blocked_by_reservation_watches = set()
        unused_reserved_events = 0
        in_reserved_state = False

        # Sort candidates by detect_ms
        n_cands = len(candidates)
        cand_ptr = 0

        t = split_ms
        while t <= end_ms:
            # 1. Enqueue new parent arrivals
            while cand_ptr < n_cands and candidates[cand_ptr]['detect_ms'] <= t:
                c = candidates[cand_ptr]
                if t < c['deadline_ms']:
                    pending_watches.append(c)
                cand_ptr += 1

            # 2. Activate scheduled children whose break_close has arrived
            i_ch = 0
            while i_ch < len(scheduled_children):
                ch = scheduled_children[i_ch]
                if t >= ch['break_close']:
                    if t < ch['expire_ms']:
                        pending_children.append(ch)
                    scheduled_children.pop(i_ch)
                else:
                    i_ch += 1

            # Check if any watches currently expired
            expired_now = [w for w in pending_watches if t >= w['deadline_ms']]
            for exp_w in expired_now:
                if exp_w['id'] in blocked_by_reservation_watches:
                    displaced_watches.add(exp_w['id'])

            # 3. Expire stale watches and children
            pending_watches = [w for w in pending_watches if t < w['deadline_ms']]
            pending_children = [ch for ch in pending_children if t < ch['expire_ms']]

            # 4. Check rolling hour limits
            hr_ago = t - 3600_000
            recent = [d for d in delivered if d['time_ms'] >= hr_ago]
            total_sent = len(recent)
            parent_sent = sum(1 for d in recent if d['type'] == 'watch')

            # Reservation tracking for Policy B
            if policy == 'B':
                if total_sent == 4 and parent_sent == 4:
                    if not in_reserved_state:
                        in_reserved_state = True
                        unused_reserved_events += 1
                else:
                    in_reserved_state = False

            # Try to deliver children first (confirmations have high priority)
            if pending_children:
                # Under Policy A: total_sent < 5
                # Under Policy B: total_sent < 5 (even if parent_sent == 4, 5th slot is open to confirmation)
                if total_sent < 5:
                    ch = pending_children.pop(0)
                    delivered.append({
                        'type': 'child',
                        'time_ms': t,
                        'symbol': ch['symbol'],
                        'parent_id': ch['parent_id'],
                        'delay_sec': (t - ch['break_close']) / 1000.0
                    })
                    # Re-check rolling hour
                    recent = [d for d in delivered if d['time_ms'] >= hr_ago]
                    total_sent = len(recent)
                    parent_sent = sum(1 for d in recent if d['type'] == 'watch')

            # Try to deliver parent watch
            pending_watches.sort(key=lambda x: (-x['quality_score'], -x['rvol'], x['detect_ms'], x['id']))

            for i, w in enumerate(pending_watches):
                if total_sent >= 5:
                    break
                if policy == 'B' and parent_sent >= 4:
                    # Slot 5 is reserved for confirmations
                    # Check if this watch would otherwise be allowed by symbol limits
                    sym_recent = [d for d in recent if d['symbol'] == w['symbol']]
                    last_sym_time = max([d['time_ms'] for d in sym_recent], default=None)
                    if len(sym_recent) < 2 and (last_sym_time is None or (t - last_sym_time) >= 900_000):
                        blocked_by_reservation_watches.add(w['id'])
                    break
                
                # Check per-symbol limits
                sym_recent = [d for d in recent if d['symbol'] == w['symbol']]
                if len(sym_recent) >= 2:
                    continue
                last_sym_time = max([d['time_ms'] for d in sym_recent], default=None)
                if last_sym_time is not None and (t - last_sym_time) < 900_000:
                    continue

                # Allowed! Deliver this watch
                delivered_w = pending_watches.pop(i)
                blocked_by_reservation_watches.discard(delivered_w['id'])
                delay_sec = (t - delivered_w['close_ms']) / 1000.0
                delivered_entry = {
                    'type': 'watch',
                    'time_ms': t,
                    'symbol': delivered_w['symbol'],
                    'id': delivered_w['id'],
                    'delay_sec': delay_sec,
                    'before_first_close': t < delivered_w['start_ms'] + 2 * m.MINUTE,
                }
                delivered.append(delivered_entry)

                # Check if this watch generates a qualifying child confirmation
                str_id = str(delivered_w['id'])
                if str_id in known_breaks:
                    b_info = known_breaks[str_id]
                    if t < b_info['break_close']:
                        scheduled_children.append({
                            'symbol': delivered_w['symbol'],
                            'parent_id': delivered_w['id'],
                            'break_close': b_info['break_close'],
                            'expire_ms': b_info['break_close'] + m.MINUTE,
                            'quality_score': delivered_w['quality_score'],
                            'rvol': delivered_w['rvol']
                        })

                # Update recent
                recent = [d for d in delivered if d['time_ms'] >= hr_ago]
                total_sent = len(recent)
                parent_sent = sum(1 for d in recent if d['type'] == 'watch')
                break

            t += 1000

        # Compute summary metrics for this policy
        watches_del = [d for d in delivered if d['type'] == 'watch']
        children_del = [d for d in delivered if d['type'] == 'child']
        delays = [d['delay_sec'] for d in watches_del]
        before_first = sum(1 for d in watches_del if d['before_first_close'])

        results[policy] = {
            'timely_parent_deliveries': len(watches_del),
            'child_deliveries': len(children_del),
            'parents_before_first_close': before_first,
            'median_parent_delay_sec': float(np.median(delays)) if delays else 0,
            'p90_parent_delay_sec': float(np.percentile(delays, 90)) if delays else 0,
            'max_parent_delay_sec': float(np.max(delays)) if delays else 0,
            'fresh_watches_displaced_by_reservation': len(displaced_watches) if policy == 'B' else 0,
            'unused_reserved_capacity_periods': unused_reserved_events if policy == 'B' else 0,
        }

    return results

if __name__ == '__main__':
    print("--- SIMULATION 1: Held-out Historical Watches Cohort (N=477) ---")
    res1 = run_simulation(include_all_candidates=False)
    print(json.dumps(res1, indent=2))

    print("\n--- SIMULATION 2: Full Retained Candidates Pool (N=4564) ---")
    res2 = run_simulation(include_all_candidates=True)
    print(json.dumps(res2, indent=2))
