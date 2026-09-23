"""One local network namespace for the app and Ollama; no remote inference."""

import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
children = []


def stop(*_):
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
    for child in reversed(children):
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
    children.clear()


def on_signal(*_):
    stop()
    sys.exit(0)


signal.signal(signal.SIGTERM, on_signal)
signal.signal(signal.SIGINT, on_signal)


def run(args):
    subprocess.run(args, check=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if mode not in ("prepare", "serve", "check"):
        print("Use prepare, serve or check", file=sys.stderr)
        return 2
    server = subprocess.Popen(["ollama", "serve"])
    children.append(server)
    for _ in range(60):
        if server.poll() is not None:
            raise RuntimeError("Ollama failed to start")
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1).close()
            break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError("Ollama did not become ready")
    if mode == "prepare":
        if not os.environ.get("HF_TOKEN", "").strip():
            print(
                "HF_TOKEN is missing. Accept pyannote conditions and add your Read token to .env; see README.",
                file=sys.stderr,
            )
            return 1
        run([sys.executable, "scripts/download_models.py", "asr", "diarization"])
        run(["ollama", "pull", os.environ.get("OLLAMA_MODEL", "qwen3:4b")])
    result = subprocess.run([sys.executable, "scripts/doctor.py"])
    if result.returncode:
        print("Not ready. Run: docker compose run --rm setup", file=sys.stderr)
        return result.returncode
    if mode in ("prepare", "check"):
        print("Models and runtime are ready.", flush=True)
        return 0
    print("HackAlem ready: http://127.0.0.1:8000", flush=True)
    web = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]
    )
    children.append(web)
    while web.poll() is None:
        if server.poll() is not None:
            raise RuntimeError("Local Ollama process stopped")
        time.sleep(1)
    return web.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(
            f"Container stopped: {type(exc).__name__}. Check the messages above and README.",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        stop()
