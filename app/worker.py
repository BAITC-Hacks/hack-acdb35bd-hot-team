"""One process per stage: frees model memory before starting the next stage."""

import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
import sys
import time
from pathlib import Path
from . import store
from .config import (
    DATA,
    ASR_MODEL,
    ASR_BACKEND,
    LLM_MODEL,
    DIAR_MODEL,
    LLM_BACKEND,
    OLLAMA_URL,
    OLLAMA_MODEL,
)
from .analysis import parse_result, ground_protocol, make_prompt

# Defense in depth: inference processes may only connect to loopback services.
# Model downloads happen in scripts/download_models.py, never in this process.
import socket
import ipaddress

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


def _local_address(address):
    if isinstance(address, tuple):
        host = address[0]
        if host == "localhost":
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise RuntimeError("External networking is disabled during inference")


def _connect(sock, address):
    _local_address(address)
    return _original_connect(sock, address)


def _connect_ex(sock, address):
    _local_address(address)
    return _original_connect_ex(sock, address)


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
os.environ.pop("HF_TOKEN", None)


def cached(repo):
    from huggingface_hub import snapshot_download

    if Path(repo).is_dir():
        return str(Path(repo).resolve())
    return snapshot_download(repo, local_files_only=True)


def transcribe(m):
    path = str(DATA / m["id"] / "audio.wav")
    language = None if m.get("language") == "auto" else m.get("language", "kk")
    if ASR_BACKEND == "mlx":
        import mlx_whisper

        result = mlx_whisper.transcribe(
            path,
            path_or_hf_repo=cached(ASR_MODEL),
            language=language,
            task="transcribe",
            condition_on_previous_text=False,
            verbose=False,
        )
        raw = result["segments"]
        m["detected_language"] = result.get("language")
    else:
        from faster_whisper import WhisperModel

        model = WhisperModel(cached(ASR_MODEL), device="cpu", compute_type="int8")
        segments, info = model.transcribe(
            path,
            language=language,
            task="transcribe",
            vad_filter=True,
            condition_on_previous_text=False,
        )
        raw = [
            dict(
                start=s.start,
                end=s.end,
                text=s.text,
                avg_logprob=s.avg_logprob,
                compression_ratio=s.compression_ratio,
            )
            for s in segments
        ]
        m["detected_language"] = info.language
    m["segments"] = [
        dict(
            id=i,
            start=round(s["start"], 2),
            end=round(min(s["end"], m["duration"]), 2),
            text=s["text"].strip(),
            speaker="SPEAKER_UNKNOWN",
            uncertain=s.get("avg_logprob", 0) < -1
            or s.get("compression_ratio", 0) > 2.4,
        )
        for i, s in enumerate(raw)
        if s["text"].strip() and s["start"] < m["duration"]
    ]
    m["speakers"] = {"SPEAKER_UNKNOWN": "Участник не определён"}
    m["diarized"] = False
    m["protocol"] = None
    m["warnings"] = [
        "Распознавание требует проверки, особенно имена, числа и смешанная речь."
    ]
    m["status"] = "transcribed"


def diarize(m):
    import torch
    import soundfile as sf
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(cached(DIAR_MODEL))
    audio, sr = sf.read(str(DATA / m["id"] / "audio.wav"), dtype="float32")
    waveform = torch.from_numpy(audio).unsqueeze(0)
    options = {"num_speakers": m["num_speakers"]} if m.get("num_speakers") else {}
    result = pipeline({"waveform": waveform, "sample_rate": sr}, **options)
    annotation = result.exclusive_speaker_diarization
    turns = [
        (turn.start, turn.end, speaker)
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]
    for s in m["segments"]:
        overlaps = [
            (max(0, min(s["end"], end) - max(s["start"], start)), speaker)
            for start, end, speaker in turns
        ]
        overlap, speaker = max(overlaps, default=(0, "SPEAKER_UNKNOWN"))
        s["speaker"] = speaker if overlap else "SPEAKER_UNKNOWN"
        if overlap < (s["end"] - s["start"]) * 0.6:
            s["uncertain"] = True
    labels = sorted(set(s["speaker"] for s in m["segments"]))
    m["speakers"] = {
        label: m.get("speakers", {}).get(
            label,
            "Участник не определён"
            if label == "SPEAKER_UNKNOWN"
            else f"Спикер {i + 1}",
        )
        for i, label in enumerate(labels)
    }
    m["diarized"] = True
    m["protocol"] = None
    m["status"] = "transcribed"
    m["warnings"] = [
        "Сопоставьте спикеров с именами. На длинных репликах со сменой говорящего возможны ошибки."
    ]


def analyze(m):
    if LLM_BACKEND == "ollama":
        from urllib.parse import urlparse
        import httpx

        url = urlparse(OLLAMA_URL)
        if (
            url.scheme != "http"
            or url.hostname not in ("127.0.0.1", "localhost", "::1")
            or url.username
            or url.password
        ):
            raise ValueError("Only local Ollama is allowed")
        response = httpx.post(
            OLLAMA_URL.rstrip("/") + "/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "think": False,
                "format": "json",
                "messages": [{"role": "user", "content": make_prompt(m)}],
                "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 3500},
                "keep_alive": 0,
            },
            timeout=1800,
            trust_env=False,
        )
        response.raise_for_status()
        result = response.json()["message"]["content"]
    else:
        from mlx_lm import load, generate

        model, tokenizer = load(cached(LLM_MODEL))
        prompt = tokenizer.apply_chat_template(
            [{"role": "user", "content": make_prompt(m)}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        from mlx_lm.sample_utils import make_sampler

        result = generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=3500,
            sampler=make_sampler(temp=0),
            verbose=False,
        )
    protocol, warnings = ground_protocol(parse_result(result), m)
    m["protocol"] = protocol
    m["warnings"] = warnings + [
        "ИИ подготовил черновик. Проверьте поручения и подтвердите протокол."
    ]
    m["status"] = "ready"


def run(ident, stage):
    m = store.get(ident)
    if not m:
        raise ValueError("Meeting not found")
    started = time.monotonic()
    m.update(status="processing", stage=stage, error=None)
    store.save(m)
    try:
        {"transcribe": transcribe, "diarize": diarize, "analyze": analyze}[stage](m)
        m.setdefault("timings", {})[stage] = round(time.monotonic() - started, 1)
    except Exception as exc:
        # Avoid persisting URLs, access tokens or model output from library exceptions.
        m.update(
            status="error",
            error=f"Этап {stage} завершился ошибкой ({type(exc).__name__}). Проверьте установку, наличие локальной модели и длину транскрипта. Повторите этап.",
        )
        print(type(exc).__name__, flush=True)
        if isinstance(exc, ValueError) and str(exc).startswith(
            ("Транскрипт слишком", "Модель не вернула")
        ):
            m["error"] = str(exc)
    store.save(m)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
