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
from . import store, telegram_bot
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
from .schemas import ProcessRequest, TranscriptEdit, Protocol, TaskMove
from .export import docx_bytes, pdf_bytes

jobs = queue.Queue()
mutation_lock = store.mutation_lock
active_process = None


def run_job(ident, stage):
    global active_process
    steps = ("transcribe", "diarize") if stage == "prepare" else (stage,)
    for step in steps:
        command = [sys.executable, "-m", "app.worker", ident, step]
        if stage == "prepare":
            command.append("--combined")
        active_process = subprocess.Popen(command, cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if active_process.wait(timeout=7200):
            raise RuntimeError("Worker stopped")
        result = store.get(ident)
        if not result or result["status"] == "error":
            break


def process_jobs():
    global active_process
    while True:
        job = jobs.get()
        if job is None:
            return
        ident, stage = job
        try:
            run_job(ident, stage)
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
    telegram_bot.init()
    bot_stop = threading.Event()
    bot_thread = None
    if telegram_bot.enabled():
        bot_thread = threading.Thread(target=telegram_bot.poll, args=(bot_stop,), daemon=True)
        bot_thread.start()
    thread = threading.Thread(target=process_jobs, daemon=True)
    thread.start()
    yield
    bot_stop.set()
    if bot_thread:
        bot_thread.join(timeout=36)
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
            key: {
                "name": OLLAMA_MODEL if key == "llm" and LLM_BACKEND == "ollama" else repo,
                "cached": available(repo),
            }
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
        if body.stage not in ("transcribe", "prepare") and not m["segments"]:
            raise HTTPException(409, "Сначала получите транскрипт")
        repos = [ASR_MODEL, DIAR_MODEL] if body.stage == "prepare" else [
            {"transcribe": ASR_MODEL, "diarize": DIAR_MODEL, "analyze": LLM_MODEL}[body.stage]]
        if not all(available(repo) for repo in repos):
            raise HTTPException(
                409,
                "Модель ещё не скачана. Выполните scripts/download_models.py по README.",
            )
        m.update(
            status="queued",
            progress={"stage": body.stage, "percent": 0, "label": "В очереди", "estimated": False},
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
        previous = {s["id"]: s for s in m["segments"]}
        segments = []
        for item in body.segments:
            segment = item.model_dump()
            old = previous.get(item.id, {})
            # Timing belongs to the original audio/text alignment, not to edits
            # supplied by a client. Preserve it only while text and bounds match.
            if all(segment[k] == old.get(k) for k in ("text", "start", "end")):
                segment["words"] = old.get("words", [])
                if old.get("alternative_text"):
                    segment["alternative_text"] = old["alternative_text"]
            else:
                segment["words"] = []
            segments.append(segment)
        m.update(
            segments=segments,
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
        if any(i not in valid_ids for t in body.tasks
               for i in t.source_ids + [q.source_id for q in t.context_evidence]):
            raise HTTPException(422, "Поручение ссылается на отсутствующую реплику")
        protocol = body.model_dump(mode="json")
        previous = m.get("protocol") or {}
        sources = previous.get("sources", {})
        protocol["sources"] = {
            "summary": sources.get("summary", []) if body.summary == previous.get("summary") else [],
            "decisions": [s for s in sources.get("decisions", []) if s["text"] in body.decisions],
        }
        m.update(protocol=protocol, status="ready")
        return store.save(m)


@app.get("/api/meetings/{ident}/audio")
def audio(ident: str):
    meeting(ident)
    path = DATA / ident / "audio.wav"
    if not path.exists():
        raise HTTPException(404, "Аудиозапись отсутствует")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/tasks")
def task_board():
    return [
        {**task, "meeting_id": m["id"], "meeting_title": m["title"],
         "approved": bool(m["protocol"].get("approved")),
         "busy": m["status"] in ("queued", "processing")}
        for m in store.all_meetings()
        for task in (m.get("protocol") or {}).get("tasks", [])
    ]


@app.patch("/api/meetings/{ident}/tasks/{task_id}")
def move_task(ident: str, task_id: str, body: TaskMove):
    with mutation_lock:
        m = meeting(ident)
        editable(m)
        protocol = m.get("protocol") or {}
        task = next((t for t in protocol.get("tasks", []) if t.get("id") == task_id), None)
        if task is None:
            raise HTTPException(404, "Поручение удалено или протокол сформирован заново. Обновите доску.")
        if not protocol.get("approved") or task.get("needs_review", True):
            raise HTTPException(409, "Сначала проверьте поручение и подтвердите протокол встречи")
        if task.get("status", "open") != body.expected_status:
            raise HTTPException(409, "Статус уже изменился. Доска будет обновлена.")
        task["status"] = body.status
        store.save(m)
        if body.status == "done":
            with store.connect() as con:
                con.execute("UPDATE tg_actions SET due=NULL WHERE meeting=? AND signature=?",
                            (ident, telegram_bot.task_signature(task)))
        return task


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
            con.execute("DELETE FROM tg_links WHERE meeting=?", (ident,))
        shutil.rmtree(DATA / ident, ignore_errors=True)
    return Response(status_code=204)


@app.get("/api/meetings/{ident}/telegram")
def telegram_status(ident: str):
    meeting(ident)
    with store.connect() as con:
        unknown = con.execute("SELECT COUNT(*) FROM tg_actions WHERE meeting=? AND due=-1", (ident,)).fetchone()[0]
    return {"enabled": telegram_bot.enabled(), "links": telegram_bot.links(ident), "reminders_unknown": unknown}


@app.post("/api/meetings/{ident}/telegram/invite")
def telegram_invite(ident: str, body: dict):
    with mutation_lock:
        m = meeting(ident)
        owner = body.get("owner")
        owners = {t.get("owner") for t in (m.get("protocol") or {}).get("tasks", [])}
        if not isinstance(owner, str) or not owner.strip() or owner not in owners:
            raise HTTPException(422, "Select a saved task owner")
        try:
            return telegram_bot.invite(ident, owner)
        except telegram_bot.BotError as exc:
            raise HTTPException(409, str(exc)) from None


@app.post("/api/meetings/{ident}/telegram/notify")
def telegram_notify(ident: str):
    with mutation_lock:
        m = meeting(ident)
        editable(m)
        try:
            return telegram_bot.notify(m)
        except telegram_bot.BotError as exc:
            raise HTTPException(409, str(exc)) from None


app.mount("/", StaticFiles(directory=ROOT / "app/static", html=True), name="ui")
