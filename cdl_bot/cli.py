"""
CDL Bot CLI — start, stop, restart, and monitor the bot.

Install: pip install -e . (from repo root)
Usage:   cdl-bot start | stop | restart | status | logs
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
PID_FILE = REPO_ROOT / ".cdl-bot.pid"
LOG_FILE = REPO_ROOT / ".cdl-bot.log"
ENV_FILE = Path(__file__).resolve().parent / ".env"
VENV_PYTHON = REPO_ROOT / "venv" / "bin" / "python"


def _read_pid() -> Optional[int]:
    """Read PID from file, return None if missing or stale."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        return pid
    except (ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return None


def _load_env() -> dict:
    """Load .env file into a dict."""
    env = os.environ.copy()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip("'\"")
    return env


def start():
    """Start the bot. Idempotent — no-op if already running."""
    pid = _read_pid()
    if pid:
        print(f"Bot is already running (PID {pid})")
        return

    if not ENV_FILE.exists():
        print(f"Missing {ENV_FILE}")
        print("Copy from .env.example and fill in credentials.")
        sys.exit(1)

    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    env = _load_env()

    print("Starting CDL Bot...")
    with open(LOG_FILE, "a") as log:
        proc = subprocess.Popen(
            [python, "-m", "cdl_bot.bot"],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )

    PID_FILE.write_text(str(proc.pid))
    time.sleep(2)

    if _read_pid():
        print(f"Bot started (PID {proc.pid})")
        print(f"Logs: {LOG_FILE}")
    else:
        print(f"Bot failed to start. Check logs: {LOG_FILE}")
        sys.exit(1)


def stop():
    """Stop the bot. Idempotent — no-op if not running."""
    pid = _read_pid()
    if not pid:
        print("Bot is not running")
        return

    print(f"Stopping bot (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait up to 5s for graceful shutdown
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass

    PID_FILE.unlink(missing_ok=True)
    print("Bot stopped.")


def restart():
    """Restart the bot."""
    stop()
    time.sleep(1)
    start()


def status():
    """Show bot status."""
    pid = _read_pid()
    if pid:
        print(f"Bot is running (PID {pid})")
    else:
        print("Bot is not running")


def logs():
    """Tail the bot log file."""
    if not LOG_FILE.exists():
        print("No log file yet. Start the bot first.")
        return
    try:
        subprocess.run(["tail", "-f", str(LOG_FILE)])
    except KeyboardInterrupt:
        pass


def main():
    parser = argparse.ArgumentParser(
        prog="cdl-bot",
        description="CDL Bot — lab automation for Slack",
    )
    parser.add_argument(
        "command",
        choices=["start", "stop", "restart", "status", "logs"],
        nargs="?",
        default="start",
        help="Command to run (default: start)",
    )
    args = parser.parse_args()

    commands = {
        "start": start,
        "stop": stop,
        "restart": restart,
        "status": status,
        "logs": logs,
    }
    commands[args.command]()


if __name__ == "__main__":
    main()
