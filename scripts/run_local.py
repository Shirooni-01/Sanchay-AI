"""Helper script to launch PostgreSQL daemon (if not already running) and serve Sanchay AI via Waitress on http://127.0.0.1:5000.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

# Ensure project root is on Python module search path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PG_BIN = r"C:\omkar\pgsql\binaries\postgresql-18.6.1-x86_64-pc-windows-msvc\bin"
PG_DATA = r"C:\omkar\pgsql\data"


def ensure_postgres_running() -> subprocess.Popen | None:
    is_ready_exe = os.path.join(PG_BIN, "pg_isready.exe")
    postgres_exe = os.path.join(PG_BIN, "postgres.exe")

    if not os.path.exists(is_ready_exe) or not os.path.exists(postgres_exe):
        print(f"[Notice] Portable PostgreSQL binaries not found at {PG_BIN}.")
        return None

    # Check if already accepting connections
    res = subprocess.run([is_ready_exe, "-h", "127.0.0.1", "-p", "5432", "-U", "postgres"], capture_output=True)
    if res.returncode == 0:
        print("[Database] PostgreSQL is already running and ready on port 5432.")
        return None

    pid_file = os.path.join(PG_DATA, "postmaster.pid")
    if os.path.exists(pid_file):
        try:
            os.remove(pid_file)
            print("[Database] Removed stale postmaster.pid.")
        except Exception as err:
            print(f"[Database] Notice removing pid file: {err}")

    print("[Database] Starting PostgreSQL 18.6 daemon...")
    pg_proc = subprocess.Popen([postgres_exe, "-D", PG_DATA])
    
    # Wait up to 10 seconds for it to become ready
    for _ in range(20):
        time.sleep(0.5)
        check = subprocess.run([is_ready_exe, "-h", "127.0.0.1", "-p", "5432", "-U", "postgres"], capture_output=True)
        if check.returncode == 0:
            print("[Database] PostgreSQL successfully started and accepting connections!")
            break

    return pg_proc


def main():
    pg_proc = ensure_postgres_running()

    print("\n" + "=" * 60)
    print("  Smart Vendor / Sanchay AI Local Server")
    print("  Access in browser at: http://127.0.0.1:5000")
    print("=" * 60 + "\n")

    from app import create_app
    app = create_app("development")

    try:
        from waitress import serve
        print("Serving with Waitress on http://127.0.0.1:5000 (Ctrl+C to stop)...")
        serve(app, host="127.0.0.1", port=5000, threads=6)
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        if pg_proc and pg_proc.poll() is None:
            print("Stopping PostgreSQL daemon...")
            pg_proc.terminate()


if __name__ == "__main__":
    main()
