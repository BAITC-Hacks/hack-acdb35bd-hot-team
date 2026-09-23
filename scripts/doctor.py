"""Offline first-run diagnostics. Does not output tokens or meeting content."""

import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
import importlib
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def check():
    errors = []

    def report(ok, label, fix=""):
        print(("OK   " if ok else "FAIL ") + label, flush=True)
        if not ok:
            errors.append(label)
            if fix:
                print("     " + fix, flush=True)

    report(
        (3, 11) <= sys.version_info[:2] < (3, 13),
        f"Python {platform.python_version()}",
        "Используйте Python 3.12.",
    )
    try:
        from app.config import (
            ROOT,
            DATA,
            ASR_BACKEND,
            ASR_MODEL,
            LLM_BACKEND,
            LLM_MODEL,
            DIAR_MODEL,
            OLLAMA_URL,
            OLLAMA_MODEL,
        )
    except ImportError:
        report(
            False,
            "Зависимости приложения не установлены",
            "Запустите scripts/setup.py по README.",
        )
        return 1
    report(
        ASR_BACKEND in ("mlx", "cpu"),
        f"ASR_BACKEND={ASR_BACKEND}",
        "Допустимы mlx или cpu.",
    )
    report(
        LLM_BACKEND in ("mlx", "ollama"),
        f"LLM_BACKEND={LLM_BACKEND}",
        "Допустимы mlx или ollama.",
    )
    if ASR_BACKEND == "mlx" or LLM_BACKEND == "mlx":
        report(
            platform.system() == "Darwin" and platform.machine() == "arm64",
            "MLX: Mac с Apple Silicon",
            "На другой системе используйте ASR_BACKEND=cpu и LLM_BACKEND=ollama.",
        )
    modules = [
        "fastapi",
        "uvicorn",
        "multipart",
        "docx",
        "reportlab",
        "soundfile",
        "pyannote.audio",
    ]
    modules += ["mlx_whisper" if ASR_BACKEND == "mlx" else "faster_whisper"]
    if LLM_BACKEND == "mlx":
        modules.append("mlx_lm")
    for name in modules:
        try:
            importlib.import_module(name)
            report(True, name)
        except Exception as exc:
            report(
                False,
                f"{name}: {type(exc).__name__}",
                "Повторите установку зависимостей по README.",
            )
    for name in ("ffmpeg", "ffprobe"):
        try:
            executable = shutil.which(name)
            result = (
                subprocess.run(
                    [executable, "-version"], capture_output=True, timeout=10
                )
                if executable
                else None
            )
            report(
                bool(result and result.returncode == 0),
                name,
                "Установите FFmpeg и добавьте bin в PATH.",
            )
        except Exception:
            report(False, name, "Проверьте установку FFmpeg.")
    for resource in ("templates/protocol.docx", "fonts/LiberationSerif-Regular.ttf",
                     "fonts/LiberationSerif-Bold.ttf", "fonts/LiberationSerif-Italic.ttf",
                     "fonts/LiberationSans-Regular.ttf"):
        report((ROOT / "app" / resource).is_file(), f"Ресурс экспорта: {resource}",
               "Обновите полный репозиторий; в Docker пересоберите образ.")
    try:
        from app.export import docx_bytes, pdf_bytes
        example = dict(title="Проверка Ә Ғ Қ Ң Ө Ұ Ү Һ І", meeting_date="2026-01-01",
                       segments=[], protocol=dict(summary="Тест экспорта", tasks=[]))
        report(docx_bytes(example).startswith(b"PK") and pdf_bytes(example).startswith(b"%PDF"),
               "Пробный экспорт DOCX/PDF с кириллицей")
    except Exception as exc:
        report(False, f"Экспорт: {type(exc).__name__}",
               "Проверьте шаблон, шрифты и зависимости python-docx/reportlab.")
    try:
        with tempfile.TemporaryFile(dir=DATA) as f:
            f.write(b"ok")
        report(True, "Папка данных доступна для записи")
    except OSError:
        report(
            False,
            "Папка данных недоступна",
            "Измените DATA_DIR в .env на доступную папку.",
        )
    models = [("asr", ASR_MODEL), ("diarization", DIAR_MODEL)]
    if LLM_BACKEND == "mlx":
        models.append(("llm", LLM_MODEL))
    for name, repo in models:
        try:
            from huggingface_hub import snapshot_download

            if not Path(repo).is_dir():
                snapshot_download(repo, local_files_only=True)
            report(True, f"Локальная модель {name}")
        except Exception:
            report(
                False,
                f"Локальная модель {name} не подготовлена",
                f"uv run --no-sync python scripts/download_models.py {name}",
            )
    if LLM_BACKEND == "ollama":
        from urllib.parse import urlparse
        import httpx

        parsed = urlparse(OLLAMA_URL)
        local = (
            parsed.scheme == "http"
            and parsed.hostname in ("127.0.0.1", "localhost", "::1")
            and not parsed.username
            and not parsed.password
        )
        report(
            local,
            "Ollama использует loopback",
            "Укажите OLLAMA_URL=http://127.0.0.1:11434",
        )
        if local:
            try:
                r = httpx.get(
                    OLLAMA_URL.rstrip("/") + "/api/tags", timeout=3, trust_env=False
                )
                r.raise_for_status()
                report(
                    any(m["name"] == OLLAMA_MODEL for m in r.json().get("models", [])),
                    f"Ollama: {OLLAMA_MODEL}",
                    f"ollama pull {OLLAMA_MODEL}",
                )
            except Exception:
                report(
                    False,
                    "Ollama недоступен",
                    "Запустите локальное приложение Ollama или ollama serve.",
                )
    print(
        "\n"
        + (
            "Все проверки пройдены."
            if not errors
            else f"Нужно исправить: {len(errors)}. Запуск не готов."
        ),
        flush=True,
    )
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(check())
