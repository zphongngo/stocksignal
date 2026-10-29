#!/usr/bin/env python3
"""Manage the StockBoard Streamlit server on Linux."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
APP_FILE = APP_DIR / "app.py"
BOT_FILE = APP_DIR / "telegram_bot.py"
BOT_ENV_FILE = APP_DIR / "Phong_telegram.env"
BOT_STATUS_FILE = APP_DIR / ".telegram_bot_status"
BOT_STOP_FILE = APP_DIR / ".telegram_bot_stop"
STATE_DIR = APP_DIR / ".stockboard"
STATE_FILE = STATE_DIR / "server.json"
LOCK_FILE = STATE_DIR / "server.lock"
LOG_FILE = APP_DIR / "stockboard.log"
BOT_LOG_FILE = APP_DIR / "telegram_bot.log"

load_dotenv(BOT_ENV_FILE)


def valid_port(value):
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be a number") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def build_parser():
    parser = argparse.ArgumentParser(prog="stock")
    commands = parser.add_subparsers(dest="command", required=True)
    start_parser = commands.add_parser("start", help="start the StockBoard server")
    start_parser.add_argument("--port", "-port", "-p", type=valid_port, default=8989)
    commands.add_parser("stop", help="stop the StockBoard server")
    commands.add_parser("status", help="show the StockBoard server status")
    return parser


def read_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_state(pid, port, bot_pid=None):
    temporary_file = STATE_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps({"pid": pid, "port": port, "bot_pid": bot_pid}),
        encoding="utf-8",
    )
    os.replace(temporary_file, STATE_FILE)


def clear_state():
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass


def process_matches(pid, script_file):
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        command_line = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return False
    return os.fsencode(str(script_file)) in command_line.split(b"\0")


def find_processes(script_file):
    matches = []
    try:
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit() and process_matches(int(entry.name), script_file):
                matches.append(int(entry.name))
    except OSError:
        pass
    return matches


def launch_bot():
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        return None
    if not BOT_FILE.is_file():
        print(f"Telegram bot script not found: {BOT_FILE}", file=sys.stderr)
        return None

    BOT_STOP_FILE.unlink(missing_ok=True)
    try:
        with BOT_LOG_FILE.open("ab") as log_file:
            process = subprocess.Popen(
                [sys.executable, str(BOT_FILE)],
                cwd=APP_DIR,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        print(f"Could not start Telegram bot: {exc}", file=sys.stderr)
        return None
    return process.pid


def start_server(port):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        state = read_state() or {}
        app_pids = find_processes(APP_FILE)
        app_pid = app_pids[0] if app_pids else None
        if app_pid:
            port = state.get("port", port)
            print(f"StockBoard app is already running (PID {app_pid}).")
        else:
            command = [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                f"--server.port={port}",
                str(APP_FILE),
                "--server.headless=true",
                "--browser.gatherUsageStats=false",
            ]
            try:
                with LOG_FILE.open("ab") as log_file:
                    process = subprocess.Popen(
                        command,
                        cwd=APP_DIR,
                        stdin=subprocess.DEVNULL,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                    )
            except OSError as exc:
                print(f"Could not start StockBoard: {exc}", file=sys.stderr)
                return 1
            app_pid = process.pid
            print(f"StockBoard app started at http://127.0.0.1:{port} (PID {app_pid}).")

        bot_pids = find_processes(BOT_FILE)
        bot_pid = bot_pids[0] if bot_pids else None
        if bot_pid:
            print(f"Telegram bot is already running (PID {bot_pid}).")
        elif os.getenv("TELEGRAM_BOT_TOKEN"):
            bot_pid = launch_bot()
            if bot_pid:
                print(f"Telegram bot started (PID {bot_pid}); log: {BOT_LOG_FILE}")
        else:
            print("Telegram bot not started: TELEGRAM_BOT_TOKEN is not configured.")

        write_state(app_pid, port, bot_pid)
        print(f"Log file: {LOG_FILE}")
    return 0


def stop_processes(script_file, pids, graceful=False):
    pids = [pid for pid in set(pids) if process_matches(pid, script_file)]
    if not pids:
        return

    if graceful:
        BOT_STOP_FILE.write_text("stop", encoding="ascii")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            pids = [pid for pid in pids if process_matches(pid, script_file)]
            if not pids:
                return
            time.sleep(0.2)

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pids = [pid for pid in pids if process_matches(pid, script_file)]
        if not pids:
            return
        time.sleep(0.2)

    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def stop_server():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        app_pids = find_processes(APP_FILE)
        bot_pids = find_processes(BOT_FILE)
        if not app_pids and not bot_pids:
            clear_state()
            BOT_STOP_FILE.unlink(missing_ok=True)
            print("StockBoard app and Telegram bot are not running.")
            return 0

        stop_processes(BOT_FILE, bot_pids, graceful=True)
        stop_processes(APP_FILE, app_pids)
        clear_state()
        BOT_STOP_FILE.unlink(missing_ok=True)
        print("StockBoard app and Telegram bot stopped.")
    return 0


def show_status():
    state = read_state() or {}
    app_pids = find_processes(APP_FILE)
    bot_pids = find_processes(BOT_FILE)
    if app_pids:
        port = state.get("port", 8989)
        print(f"StockBoard app: running (PID {app_pids[0]}), port {port}.")
        print(f"URL: http://127.0.0.1:{port}")
    else:
        print("StockBoard app: stopped.")
    if bot_pids:
        print(f"Telegram bot: running (PID {bot_pids[0]}).")
    else:
        print("Telegram bot: stopped.")
    if app_pids:
        print(f"App log: {LOG_FILE}")
    if bot_pids:
        print(f"Bot log: {BOT_LOG_FILE}")
    if state and not app_pids and not bot_pids:
        clear_state()
    return 0


def main():
    args = build_parser().parse_args()
    if args.command == "start":
        return start_server(args.port)
    if args.command == "stop":
        return stop_server()
    return show_status()


if __name__ == "__main__":
    raise SystemExit(main())