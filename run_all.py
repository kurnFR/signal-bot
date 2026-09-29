#!/usr/bin/env python3
"""
Single entry point: starts every process the web dashboard depends on, and
restarts any of them if they crash. Run this instead of starting each
run_*.py / paper/retailbot2.py / Screen/screening.py by hand in separate
tmux sessions.

    python3 run_all.py
    python3 run_all.py --skip retailbot2 --skip screener   # only the core stack
    python3 run_all.py --only web_server --only paper_engine

Each process runs as its own OS subprocess (not a thread) -- deliberately.
Some of these are asyncio-based (collectors), some are blocking sync loops
(paper_engine, retailbot2, screening), and uvicorn wants to manage its own
event loop for the web server. Mixing all of that into one interpreter via
threads is fragile (one process's crash or a stuck C extension can affect
shared interpreter state); separate OS processes give clean crash isolation
and match how you'd run these manually, just supervised from one place.

If Ctrl+C'd, every child is asked to terminate (SIGTERM) and given a few
seconds before being killed outright, so nothing is left orphaned.

Each process is independently optional at the config level already (the
News AI workers and retailbot2/screener all exit cleanly or run in a
reduced/no-alert mode if their API keys aren't set -- see their own
files). This script's job is only to keep whatever you DO want running,
running.
"""
import sys
import os
import subprocess
import threading
import time
import signal
import argparse
import logging

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_all")

RESTART_BACKOFF_SECONDS = 5

# name -> command. Order doesn't matter functionally (each is independent),
# but this is roughly "most people want this" -> "opt-in extras".
PROCESSES = {
    "web_server":   [sys.executable, "run_web.py", "--port", os.getenv("WEB_PORT", "8050")],
    "collectors":   [sys.executable, "run_collectors.py"],
    "paper_engine": [sys.executable, "run_paper_engine.py"],
    "news_ai":      [sys.executable, "run_news_ai.py"],
    "retailbot2":   [sys.executable, "paper/retailbot2.py"],
    "screener":     [sys.executable, "Screen/screening.py"],
}

_shutdown = threading.Event()
_children = {}  # name -> current subprocess.Popen, for shutdown to reach
_children_lock = threading.Lock()


def _run_and_restart(name, cmd):
    """Runs one process forever, restarting it (after a short backoff) any
    time it exits, until shutdown is requested. A crash-looping process
    (e.g. permanently missing required config) shows up as a repeating log
    line here rather than silently going dark -- that's intentional."""
    while not _shutdown.is_set():
        logger.info(f"[{name}] starting: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd)
        with _children_lock:
            _children[name] = proc

        exit_code = proc.wait()
        with _children_lock:
            _children.pop(name, None)

        if _shutdown.is_set():
            return

        level = logging.INFO if exit_code == 0 else logging.WARNING
        logger.log(level, f"[{name}] exited (code {exit_code}); restarting in {RESTART_BACKOFF_SECONDS}s")
        _shutdown.wait(RESTART_BACKOFF_SECONDS)


def _shutdown_all():
    logger.info("Shutting down -- stopping all child processes...")
    _shutdown.set()
    with _children_lock:
        procs = list(_children.items())
    for name, proc in procs:
        logger.info(f"[{name}] sending SIGTERM (pid {proc.pid})")
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    deadline = time.time() + 10
    for name, proc in procs:
        remaining = max(0, deadline - time.time())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            logger.warning(f"[{name}] did not stop in time, killing (pid {proc.pid})")
            proc.kill()
    logger.info("All processes stopped.")


def main():
    parser = argparse.ArgumentParser(description="Start and supervise every process the dashboard needs.")
    parser.add_argument("--skip", action="append", default=[], choices=PROCESSES.keys(),
                         help="Process(es) to NOT start. Repeatable.")
    parser.add_argument("--only", action="append", default=[], choices=PROCESSES.keys(),
                         help="Start ONLY this process (repeatable). Overrides --skip.")
    args = parser.parse_args()

    to_run = dict(PROCESSES)
    if args.only:
        to_run = {name: cmd for name, cmd in PROCESSES.items() if name in args.only}
    else:
        to_run = {name: cmd for name, cmd in PROCESSES.items() if name not in args.skip}

    if not to_run:
        logger.error("Nothing selected to run (check --skip/--only). Exiting.")
        return

    logger.info("==================================================================")
    logger.info("  Starting Signal Bot -- all processes")
    logger.info(f"  Running: {', '.join(to_run.keys())}")
    if args.skip:
        logger.info(f"  Skipped: {', '.join(args.skip)}")
    logger.info("==================================================================")

    def _handle_signal(signum, frame):
        _shutdown_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    threads = []
    for name, cmd in to_run.items():
        t = threading.Thread(target=_run_and_restart, args=(name, cmd), daemon=True, name=name)
        t.start()
        threads.append(t)
        time.sleep(1)  # stagger startup slightly -- easier to read the logs, and
                        # avoids every process's own startup-time DB pool creation
                        # hitting MySQL in the exact same instant

    try:
        while not _shutdown.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown_all()


if __name__ == "__main__":
    main()
