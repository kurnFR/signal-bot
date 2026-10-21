#!/usr/bin/env python3
"""
Launcher script for the Crypto Signal Bot Web Dashboard.
Usage:
    python3 run_web.py --port 8050
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Crypto Signal Bot Web Dashboard")
    parser.add_argument("--port", type=int, default=8050, help="Port to listen on (default: 8050)")
    parser.add_argument("--host", type=str, default=os.getenv("SIGNAL_BOT_HOST", "127.0.0.1"),
                        help="Bind address (default: SIGNAL_BOT_HOST or 127.0.0.1; use 0.0.0.0 only behind a firewall/reverse proxy)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    import uvicorn
    print(f"\n==================================================================")
    print(f"  ⚡ Crypto Signal Bot — Quantitative Trading Platform")
    shown_host = "localhost" if args.host in ("127.0.0.1", "localhost") else args.host
    print(f"  Listening on {args.host}:{args.port}  ->  http://{shown_host}:{args.port}")
    if args.host in ("0.0.0.0", "::"):
        print("  WARNING: bound to all interfaces. Put this behind TLS and a firewall.")
    print(f"==================================================================\n")
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=args.reload)
