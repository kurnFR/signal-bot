"""
Quick standalone check: can this machine reach Binance's FUTURES REST API
(fapi.binance.com)? Run this BEFORE building/running the funding rate
backfill -- no point building on an access path that's blocked.

This only tests REST (needed for funding rate history). It does NOT test
the futures WebSocket (fstream.binance.com) -- that was already found to
be geo-restricted for this deployment earlier (Step 1), which is why
ENABLE_FUTURES defaults to false and live futures streaming stays off.
REST and WS are separate Binance subdomains/services and can have
different access outcomes.

Usage:
    python3 check_futures_access.py
"""
import requests

FUTURES_PING_URL = "https://fapi.binance.com/fapi/v1/ping"
FUTURES_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"


def main():
    print("Checking Binance Futures REST API access...")

    try:
        resp = requests.get(FUTURES_PING_URL, timeout=10)
        resp.raise_for_status()
        print(f"  [OK] Ping succeeded (status {resp.status_code})")
    except requests.RequestException as e:
        print(f"  [FAIL] Ping failed: {e}")
        print("\nCannot reach fapi.binance.com. This usually means a geo-restriction")
        print("or network block on Binance Futures from your location/network -- the")
        print("same kind of restriction found earlier for the futures WebSocket.")
        print("Funding rate backfill will not work until this is resolved (VPN, different")
        print("network, etc.) -- let me know if you want to try a workaround, or if you'd")
        print("rather pursue a different data source in the meantime.")
        return

    try:
        resp = requests.get(FUTURES_FUNDING_URL, params={"symbol": "BTCUSDT", "limit": 5}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print(f"  [OK] Funding rate endpoint returned {len(data)} sample rows for BTCUSDT")
        if data:
            print(f"       Most recent: {data[-1]}")
    except requests.RequestException as e:
        print(f"  [FAIL] Funding rate endpoint failed: {e}")
        return

    print("\nFutures REST access confirmed working. Safe to proceed with funding rate backfill.")


if __name__ == "__main__":
    main()
