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
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host address (default: 0.0.0.0)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    import uvicorn
    print(f"\n==================================================================")
    print(f"  ⚡ Crypto Signal Bot — Quantitative Trading Platform")
    print(f"  Access UI at: http://localhost:{args.port} or http://192.168.1.30:{args.port}")
    print(f"==================================================================\n")
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=args.reload)
