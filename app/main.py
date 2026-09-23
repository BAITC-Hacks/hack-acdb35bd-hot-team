import json
import queue
import shutil
import subprocess
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import store
from .config import (
    DATA,
    ROOT,
    MAX_UPLOAD,
    ASR_MODEL,
    LLM_MODEL,
    DIAR_MODEL,
    LLM_BACKEND,
    OLLAMA_URL,
    OLLAMA_MODEL,
)
from .schemas import ProcessRequest, TranscriptEdit, Protocol
from .export import docx_bytes, pdf_bytes

jobs = queue.Queue()
mutation_lock = threading.Lock()
active_process = None


def process_jobs():
    global active_process
    while True:
        job = jobs.get()
        if job is None:
            return
        ident, stage = job
        try:
            active_process = subprocess.Popen(
                [sys.executable, "-m", "app.worker", ident, stage],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if active_process.wait(timeout=7200):
                raise RuntimeError("Worker stopped")
        except Exception:
            if active_process and active_process.poll() is None:
                active_process.kill()
                active_process.wait()
            m = store.get(ident)
            if m:
                m.update(
                    status="error",
                    error="Процесс обработки остановлен. Возможно, не хватило памяти. Повторите этап.",
                )
                store.save(m)
        finally:
            active_process = None
            jobs.task_done()


@asynccontextmanager
async def lifespan(app):
    store.init()
    thread = threading.Thread(target=process_jobs, daemon=True)
    thread.start()
    yield
    if active_process and active_process.poll() is None:
        active_process.terminate()
    while not jobs.empty():
        try:
            jobs.get_nowait()
            jobs.task_done()
        except queue.Empty:
            break
    jobs.put(None)
    thread.join(timeout=5)


app = FastAPI(title="HackAlem Minutes", lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
)


@app.middleware("http")
async def local_origin(request: Request, call_next):
    from urllib.parse import urlparse

    origin = request.headers.get("origin")
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and origin
        and urlparse(origin).netloc != request.headers.get("host")
    ):
        return Response("Cross-origin writes are disabled", status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"
    )
    return response


def meeting(ident):
    m = store.get(ident)
    if not m:
        raise HTTPException(404, "Совещание не найдено")
    return m


def editable(m):
    if m["status"] in ("queued", "processing"):
        raise HTTPException(409, "Дождитесь окончания обработки")


def available(repo):
    if repo == LLM_MODEL and LLM_BACKEND == "ollama":
        from urllib.parse import urlparse
        import httpx

        if urlparse(OLLAMA_URL).hostname not in ("127.0.0.1", "localhost", "::1"):
            return False
        try:
            response = httpx.get(
                OLLAMA_URL.rstrip("/") + "/api/tags", timeout=2, trust_env=False
            )
            response.raise_for_status()
            return any(
                m["name"] == OLLAMA_MODEL for m in response.json().get("models", [])
            )
        except Exception:
            return False
    if Path(repo).is_dir():
        return True
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(repo, local_files_only=True)
        return True
    except Exception:
        return False


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "local_only": True,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "models": {
            key: {"name": repo, "cached": available(repo)}
            for key, repo in [
                ("asr", ASR_MODEL),
                ("llm", LLM_MODEL),
                ("diarization", DIAR_MODEL),
            ]
        },
    }


@app.get("/api/meetings")
def list_meetings():
    return [
        {k: v for k, v in m.items() if k not in ("segments", "protocol")}
        | {"task_count": len((m.get("protocol") or {}).get("tasks", []))}
        for m in store.all_meetings()
    ]


@app.post("/api/meetings", status_code=201)
async def create_meeting(
    title: str = Form(..., max_length=200),
    meeting_date: date = Form(...),
    participants: str = Form("", max_length=3000),
    file: UploadFile = File(...),
):
    if not title.strip():
        raise HTTPException(422, "Укажите название")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg", ".flac", ".mov"}:
        raise HTTPException(
            422, "Поддерживаются MP3, WAV, M4A, MP4, WEBM, OGG, FLAC и MOV"
        )
    if not shutil.which("ffmpeg"):
        raise HTTPException(503, "Установите FFmpeg по инструкции README")
    ident = uuid.uuid4().hex
    folder = DATA / ident
    folder.mkdir()
    source = folder / ("source" + suffix)
    try:
        size = 0
        with source.open("wb") as dest:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "Максимальный размер — 250 МБ")
                dest.write(chunk)
        import asyncio

        def convert():
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            duration = float(json.loads(probe.stdout)["format"]["duration"])
            if duration <= 0 or duration > 3600:
                raise HTTPException(
                    422, "Длительность записи должна быть от 1 секунды до 60 минут"
                )
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-vn",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    str(folder / "audio.wav"),
                ],
                check=True,
                capture_output=True,
                timeout=180,
            )
            return duration

        duration = await asyncio.to_thread(convert)
        source.unlink()
        now = datetime.now(timezone.utc).isoformat()
        return store.save(
            dict(
                id=ident,
                title=title.strip(),
                meeting_date=str(meeting_date),
                participants=[p.strip() for p in participants.split(",") if p.strip()],
                duration=round(duration, 2),
                status="uploaded",
                stage=None,
                created_at=now,
                segments=[],
                speakers={},
                protocol=None,
                warnings=[],
                diarized=False,
                timings={},
                error=None,
            )
        )
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(
            422, "Не удалось прочитать аудио. Проверьте файл и наличие FFmpeg/ffprobe."
        )
    finally:
        await file.close()


@app.get("/api/meetings/{ident}")
def get_meeting(ident: str):
    return meeting(ident)


@app.post("/api/meetings/{ident}/process", status_code=202)
def process(ident: str, body: ProcessRequest):
    with mutation_lock:
        m = meeting(ident)
        editable(m)
        if body.stage != "transcribe" and not m["segments"]:
            raise HTTPException(409, "Сначала получите транскрипт")
        repo = {"transcribe": ASR_MODEL, "diarize": DIAR_MODEL, "analyze": LLM_MODEL}[
            body.stage
        ]
        if not available(repo):
            raise HTTPException(
                409,
                "Модель ещё не скачана. Выполните scripts/download_models.py по README.",
            )
        m.update(
            status="queued",
            stage=body.stage,
            error=None,
            language=body.language,
            num_speakers=body.num_speakers,
        )
        store.save(m)
        jobs.put((ident, body.stage))
    return m


@app.put("/api/meetings/{ident}/transcript")
def update_transcript(ident: str, body: TranscriptEdit):
    with mutation_lock:
        m = meeting(ident)
        editable(m)
        if any(s.end > m["duration"] + 1 for s in body.segments):
            raise HTTPException(422, "Таймкод находится за пределами записи")
        m.update(
            segments=[s.model_dump() for s in body.segments],
            speakers=body.speakers,
            protocol=None,
            status="transcribed",
            warnings=["Транскрипт изменён. Сформируйте протокол заново."],
        )
        return store.save(m)


@app.put("/api/meetings/{ident}/protocol")
def update_protocol(ident: str, body: Protocol):
    with mutation_lock:
        m = meeting(ident)
        editable(m)
        if not m["segments"]:
            raise HTTPException(409, "Сначала получите транскрипт")
        if body.approved and any(t.needs_review for t in body.tasks):
            raise HTTPException(
                422, "Проверьте каждое поручение перед подтверждением протокола"
            )
        valid_ids = {s["id"] for s in m["segments"]}
        if any(i not in valid_ids for t in body.tasks for i in t.source_ids):
            raise HTTPException(422, "Поручение ссылается на отсутствующую реплику")
        m.update(protocol=body.model_dump(mode="json"), status="ready")
        return store.save(m)


@app.get("/api/meetings/{ident}/audio")
def audio(ident: str):
    meeting(ident)
    path = DATA / ident / "audio.wav"
    if not path.exists():
        raise HTTPException(404, "Аудиозапись отсутствует")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/meetings/{ident}/export/{fmt}")
def export(ident: str, fmt: str):
    m = meeting(ident)
    if not m["segments"]:
        raise HTTPException(409, "Сначала получите транскрипт")
    if fmt == "json":
        content = json.dumps(m, ensure_ascii=False, indent=2).encode()
        media = "application/json"
    elif fmt == "docx":
        content = docx_bytes(m)
        media = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    elif fmt == "pdf":
        content = pdf_bytes(m)
        media = "application/pdf"
    else:
        raise HTTPException(404)
    return Response(
        content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="minutes-{ident[:8]}.{fmt}"'
        },
    )


@app.delete("/api/meetings/{ident}", status_code=204)
def delete(ident: str):
    with mutation_lock:
        m = meeting(ident)
        editable(m)
        with store.connect() as con:
            con.execute("DELETE FROM meetings WHERE id=?", (ident,))
        shutil.rmtree(DATA / ident, ignore_errors=True)
    return Response(status_code=204)


app.mount("/", StaticFiles(directory=ROOT / "app/static", html=True), name="ui")
