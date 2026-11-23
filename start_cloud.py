"""Запуск облачного сервиса: aiohttp-витрина и Streamlit-дашборд."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SERVER_PORT = (os.getenv("PORT") or "8000").strip() or "8000"
DASHBOARD_PORT = (os.getenv("STREAMLIT_PORT") or "8501").strip() or "8501"


def cloud_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["MODE"] = "webhook"
    env["PORT"] = SERVER_PORT
    env["PYTHONUNBUFFERED"] = "1"
    return env


def build_commands() -> list[list[str]]:
    python = sys.executable
    return [
        [python, "server.py"],
        [
            python,
            "-m",
            "streamlit",
            "run",
            "dashboard.py",
            "--server.port",
            DASHBOARD_PORT,
            "--server.address",
            "0.0.0.0",
            "--server.headless",
            "true",
        ],
    ]


def main() -> None:
    env = cloud_environment()
    processes = [
        subprocess.Popen(command, cwd=PROJECT_DIR, env=env)
        for command in build_commands()
    ]

    def terminate_children() -> None:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    def handle_signal(_signum: int, _frame: object) -> None:
        terminate_children()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while True:
            for process in processes:
                code = process.poll()
                if code is not None:
                    print(
                        f"Процесс {process.pid} завершился с кодом {code}",
                        flush=True,
                    )
                    terminate_children()
                    raise SystemExit(code)
            time.sleep(0.5)
    except KeyboardInterrupt:
        terminate_children()


if __name__ == "__main__":
    main()
