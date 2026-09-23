"""Launch from any working directory with preflight diagnostics."""

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Порт должен быть от 1024 до 65535")
    os.chdir(ROOT)
    # Keep bytecode outside cloud-synced project folders (e.g. iCloud Documents).
    os.environ.setdefault(
        "PYTHONPYCACHEPREFIX", str(Path.home() / ".cache" / "hackalem" / "python-cache")
    )
    result = subprocess.run([sys.executable, str(ROOT / "scripts/doctor.py")])
    if result.returncode:
        return result.returncode
    try:
        with socket.socket() as sock:
            # Match uvicorn's POSIX socket setup: TIME_WAIT after a restart
            # must not be mistaken for a running server. Avoid Windows reuse
            # semantics, which can allow binding over another process.
            if os.name != "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", args.port))
    except OSError:
        print(
            f"Порт {args.port} занят. Попробуйте: uv run --no-sync python scripts/start.py --port {args.port + 1}"
        )
        return 1
    print(f"\nОткройте http://127.0.0.1:{args.port} · Остановка: Ctrl+C", flush=True)
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
        ],
    )


if __name__ == "__main__":
    sys.exit(main())
