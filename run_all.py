#!/usr/bin/env python3
"""
Single entry point for supervising the configured Signal Bot processes.
The safe default set is the Phase 2A stack; paper/news engines require
separate opt-ins.

    python3 run_all.py
    python3 run_all.py --only web_server --only collectors --only screener --only wallet_tracker

Enable the paper engine or News AI explicitly when intended:

    PAPER_ENGINE_ENABLED=1 python3 run_all.py --only paper_engine
    NEWS_AI_ENABLED=1 python3 run_all.py --only news_ai

RetailBot2 was retired in the retailbot2-migration work: the worker
(paper/retailbot2.py) is deleted and there is no RETAILBOT2_ENABLED opt-in
anymore. Its strategies live in the strategy library
(backtest/strategies/retail_death_trap.py) and run through Paper Trading;
its Telegram outbox lives in paper/outbox_store.py.

The outbox worker is a separate opt-in and additionally requires
RETAILBOT2_OUTBOX_WORKER_ENABLED=1 and
TELEGRAM_DELIVERY_ENABLED=1 (or deprecated alias RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1).

Automatic migrations are disabled by default. Run python3 -m db.migrate
as a separately authorized deployment step, or explicitly opt in to automatic
startup migrations with SIGNAL_BOT_AUTO_MIGRATE=1.

Each process runs as its own OS subprocess (not a thread) for crash isolation.
If Ctrl+C'd, children receive SIGTERM. The outbox worker is never force-killed
by the supervisor after its shutdown grace period because it may still own
its MariaDB advisory lock while a Telegram transport descendant is alive or
its termination state is unverified.
"""
import sys
import os
import subprocess
import threading
import time
import signal
import argparse
import logging
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("run_all")

RESTART_BACKOFF_SECONDS = 5

def build_processes(environ=None):
    """Build supervised commands; the Telegram outbox worker is an explicit opt-in."""
    env = os.environ if environ is None else environ
    raw_port = (env.get("WEB_PORT") or "").strip()
    try:
        port_num = int(raw_port) if raw_port else 8050
        web_port = str(port_num) if (1 <= port_num <= 65535) else "8050"
    except ValueError:
        logger.warning("Malformed WEB_PORT %r; falling back to 8050", raw_port)
        web_port = "8050"

    raw_host = (env.get("SIGNAL_BOT_HOST") or "").strip()
    web_host = raw_host or "127.0.0.1"

    processes = {
        "web_server": [
            sys.executable, "run_web.py", "--port",
            web_port, "--host",
            web_host,
        ],
        "collectors": [sys.executable, "run_collectors.py"],
        "screener": [sys.executable, "Screen/screening.py"],
        "wallet_tracker": [sys.executable, "run_wallet_tracker.py"],
    }

    # Paper and news engines are held back in the safe Phase 2A default set.
    if (env.get("PAPER_ENGINE_ENABLED") or "").strip() == "1":
        processes["paper_engine"] = [sys.executable, "run_paper_engine.py"]
    if (env.get("NEWS_AI_ENABLED") or "").strip() == "1":
        processes["news_ai"] = [sys.executable, "run_news_ai.py"]

    # The RetailBot2 worker is retired (paper/retailbot2.py deleted); there is
    # no "retailbot2" supervisor entry anymore. RETAILBOT2_ENABLED is ignored.
    # The Telegram outbox worker below is the only retailbot2-named process
    # left, and it is backed by paper/outbox_store.py, not the worker.

    # Keep delivery disabled by default. Both flags are required so enabling
    # the process supervisor alone cannot accidentally enable production sends.
    if (env.get("RETAILBOT2_OUTBOX_WORKER_ENABLED") or "").strip() == "1":
        delivery_enabled = (
            (env.get("TELEGRAM_DELIVERY_ENABLED") or "").strip() == "1"
            or (env.get("RETAILBOT2_ALLOW_TELEGRAM_DELIVERY") or "").strip() == "1"
        )
        if delivery_enabled:
            processes["retailbot2_outbox"] = [
                sys.executable, "-m", "paper.outbox_worker",
                "--database", "crypto_signals",
            ]
        else:
            logger.error(
                "RetailBot2 outbox worker requested but TELEGRAM_DELIVERY_ENABLED=1 "
                "(or legacy RETAILBOT2_ALLOW_TELEGRAM_DELIVERY=1) is missing; worker will remain disabled"
            )
    report_enabled = (env.get("PAPER_REPORT_ENABLED") or "").strip()
    if report_enabled == "1" and "retailbot2_outbox" in processes:
        processes["paper_report"] = [sys.executable, "-m", "paper.report_scheduler"]
    return processes


# name -> command. Phase 2A services are enabled by default; paper_engine,
# news_ai, and the outbox worker each require explicit opt-in.
PROCESSES = build_processes()

_shutdown = threading.Event()
_children = {}  # name -> current subprocess.Popen, for shutdown to reach
_children_lock = threading.Lock()


def _run_and_restart(name, cmd):
    """Runs one process forever, restarting it (after a short backoff) any
    time it exits, until shutdown is requested. A crash-looping process
    (e.g. permanently missing required config) shows up as a repeating log
    line here rather than silently going dark -- that's intentional."""
    while True:
        # Serialize the shutdown check, process creation, and registration with
        # _shutdown_all()'s child snapshot. Otherwise shutdown can miss a child
        # spawned between its snapshot and this thread's registration.
        with _children_lock:
            if _shutdown.is_set():
                return
            logger.info(f"[{name}] starting: {' '.join(cmd)}")
            try:
                proc = subprocess.Popen(cmd)
            except OSError as exc:
                logger.warning("[%s] launch failed (%s); retrying in %ss",
                               name, exc, RESTART_BACKOFF_SECONDS)
                proc = None
            else:
                _children[name] = proc

        if proc is None:
            _shutdown.wait(RESTART_BACKOFF_SECONDS)
            continue

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
            if name == "retailbot2_outbox":
                logger.critical(
                    "[%s] has not released its safety lock after SIGTERM; refusing SIGKILL "
                    "and waiting until the worker confirms transport cleanup (pid %s)",
                    name, proc.pid,
                )
                # This may wait indefinitely if a transport process is stuck in
                # an uninterruptible state. That is intentional: killing the
                # lock owner could allow a replacement worker to send while an
                # old transport descendant is still alive.
                proc.wait()
            else:
                logger.warning(f"[{name}] did not stop in time, killing (pid {proc.pid})")
                proc.kill()
    logger.info("All processes stopped.")


def ensure_database_running():
    """Checks if database port is open; starts embedded MariaDB if available."""
    import socket
    from pathlib import Path

    raw_host = (os.getenv("MYSQL_HOST") or "").strip()
    host = raw_host or "127.0.0.1"
    raw_port = (os.getenv("MYSQL_PORT") or "").strip()
    try:
        port = int(raw_port) if raw_port else 3306
        if not (1 <= port <= 65535):
            logger.warning("MYSQL_PORT %r out of range (1-65535); falling back to 3306", raw_port)
            port = 3306
    except ValueError:
        logger.warning("Malformed MYSQL_PORT %r; falling back to 3306", raw_port)
        port = 3306

    def is_port_open():
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            return False

    if is_port_open():
        logger.info(f"Database ({host}:{port}) is reachable.")
        return None

    candidates = [
        Path(os.path.dirname(os.path.abspath(__file__))).parent / "mariadb",
        Path(os.path.dirname(os.path.abspath(__file__))) / "mariadb",
    ]
    for mariadb_root in candidates:
        mysqld_paths = list(mariadb_root.glob("**/bin/mysqld.exe"))
        ini_paths = list(mariadb_root.glob("**/my.ini"))
        if mysqld_paths and ini_paths:
            mysqld_bin = mysqld_paths[0]
            my_ini = ini_paths[0]
            logger.info("Database is not running. Starting embedded MariaDB (%s)...", mysqld_bin)
            proc = subprocess.Popen(
                [str(mysqld_bin), f"--defaults-file={str(my_ini)}", "--console"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(15):
                time.sleep(1)
                if is_port_open():
                    logger.info("Embedded MariaDB started successfully (PID %s).", proc.pid)
                    with _children_lock:
                        _children["mariadb"] = proc
                    return proc
            logger.warning("MariaDB process started (PID %s) but port %s was not open within 15s.", proc.pid, port)
            with _children_lock:
                _children["mariadb"] = proc
            return proc

    logger.warning("Database at %s:%s is not reachable and local MariaDB binary not found.", host, port)
    return None


def apply_migrations(runner=subprocess.run, environ=None) -> bool:
    """Apply migrations only when explicitly authorized.

    The safe default is to leave schema changes to a separate deployment step.
    Set SIGNAL_BOT_AUTO_MIGRATE=1 to opt in to startup migrations. The legacy
    SKIP_DB_MIGRATE flag remains supported as an explicit override.
    """
    env = os.environ if environ is None else environ
    skip_value = (env.get("SKIP_DB_MIGRATE") or "").strip().lower()
    auto_value = (env.get("SIGNAL_BOT_AUTO_MIGRATE") or "").strip().lower()

    if skip_value in ("1", "true", "yes"):
        logger.info("SKIP_DB_MIGRATE set: not running database migrations")
        return True
    if skip_value not in ("", "0", "false", "no"):
        logger.error("Invalid SKIP_DB_MIGRATE value; refusing startup migration")
        return False

    if auto_value in ("", "0", "false", "no"):
        logger.info(
            "Automatic database migrations are disabled; run python -m db.migrate "
            "as a separate deployment step or set SIGNAL_BOT_AUTO_MIGRATE=1"
        )
        return True
    if auto_value != "1":
        logger.error("Invalid SIGNAL_BOT_AUTO_MIGRATE value; refusing startup")
        return False

    logger.info("Applying database migrations (python -m db.migrate)...")
    result = runner([sys.executable, "-m", "db.migrate"])
    if result.returncode != 0:
        logger.error("Database migration failed (exit %s). Fix the error above, or set "
                     "SKIP_DB_MIGRATE=1 to start anyway.", result.returncode)
        return False
    return True


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

    ensure_database_running()

    if not apply_migrations():
        _shutdown_all()
        sys.exit(1)

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
