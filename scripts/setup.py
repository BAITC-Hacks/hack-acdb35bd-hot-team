"""Reproducible first-run setup; stdlib only, invoked through uv."""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(args):
    subprocess.run(args, cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description="Подготовка HackAlem Minutes")
    parser.add_argument(
        "--skip-models",
        action="store_true",
        help="Установить зависимости без загрузки весов (для разработчиков)",
    )
    args = parser.parse_args()
    print("HackAlem Minutes — подготовка окружения", flush=True)
    if not shutil.which("uv"):
        print("Установите uv: https://docs.astral.sh/uv/getting-started/installation/")
        return 1
    missing = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        print("Не найдено: " + ", ".join(missing))
        print(
            "macOS: brew install ffmpeg\nUbuntu: sudo apt install ffmpeg\nWindows: установите FFmpeg и добавьте его bin в PATH."
        )
        return 1
    mac = platform.system() == "Darwin" and platform.machine() == "arm64"
    backend = "mac" if mac else "cpu"
    print(f"Профиль: {backend} ({platform.system()} {platform.machine()})", flush=True)
    env_path = ROOT / ".env"
    if not env_path.exists():
        example = (ROOT / ".env_example").read_text(encoding="utf-8")
        if not mac:
            example = (
                example.replace("ASR_BACKEND=mlx", "ASR_BACKEND=cpu")
                .replace(
                    "ASR_MODEL=mlx-community/whisper-large-v3-turbo",
                    "ASR_MODEL=dropbox-dash/faster-whisper-large-v3-turbo",
                )
                .replace("LLM_BACKEND=mlx", "LLM_BACKEND=ollama")
            )
        env_path.write_text(example, encoding="utf-8")
        if os.name != "nt":
            env_path.chmod(0o600)
        print(
            "Создан .env из примера. Токен можно добавить после установки.", flush=True
        )
    else:
        print("Существующий .env сохранён без изменений.", flush=True)
    run(
        [
            "uv",
            "sync",
            "--frozen",
            "--python",
            "3.12",
            "--extra",
            backend,
            "--extra",
            "diarization",
            "--extra",
            "dev",
        ]
    )
    python = str(
        ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not args.skip_models:
        run([python, "scripts/download_models.py", "asr"] + (["llm"] if mac else []))
        if not mac:
            print(
                "Для анализа нужен локальный Ollama: https://ollama.com/\nЗапустите Ollama и выполните: ollama pull qwen3:4b",
                flush=True,
            )
        # Inspect existence only; never print credentials.
        result = subprocess.run(
            [
                python,
                "-c",
                "from app.config import ROOT; import os,sys; sys.exit(0 if os.environ.get('HF_TOKEN','').strip() else 1)",
            ],
            cwd=ROOT,
        )
        if result.returncode:
            print(
                "\nДля диаризации примите условия https://huggingface.co/pyannote/speaker-diarization-community-1"
            )
            print(
                "Создайте Read-токен: https://huggingface.co/settings/tokens и добавьте HF_TOKEN в .env."
            )
            print(
                "Затем повторите эту же команду установки: скачанные файлы будут использованы повторно."
            )
            return 1
        run([python, "scripts/download_models.py", "diarization"])
    result = subprocess.run([python, "scripts/doctor.py"], cwd=ROOT)
    if result.returncode:
        return result.returncode
    print("\nГотово. Запуск: uv run --no-sync python scripts/start.py", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError:
        print(
            "\nУстановка остановлена. Исправьте причину выше и повторите команду; .env не перезаписывается.",
            file=sys.stderr,
        )
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nУстановка прервана. Команду можно запустить повторно.")
        sys.exit(130)
