import io
import json
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from docx import Document
from app import store
from app.main import app
from app.analysis import parse_result, ground_protocol, make_prompt
from app.schemas import Protocol


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", tmp_path / "test.sqlite3")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def meeting(client):
    now = datetime.now(timezone.utc).isoformat()
    m = dict(
        id="test",
        title="Тест Ә Ғ Қ Ң Ө Ұ Ү Һ І",
        meeting_date="2026-09-23",
        participants=["Айдана"],
        duration=12,
        status="transcribed",
        created_at=now,
        segments=[
            dict(
                id=0,
                start=0,
                end=4,
                text="Айдана, подготовь отчёт завтра.",
                speaker="SPEAKER_00",
                uncertain=False,
            )
        ],
        speakers={"SPEAKER_00": "Асет"},
        protocol=None,
        warnings=[],
        diarized=False,
        timings={},
        error=None,
    )
    return store.save(m)


def test_missing_and_upload_validation(client):
    assert client.get("/api/meetings/missing").status_code == 404
    assert (
        client.post(
            "/api/meetings",
            data={"title": "Test", "meeting_date": "2026-09-23"},
            files={"file": ("x.txt", b"a")},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/meetings",
            data={"title": "Test", "meeting_date": "2026-09-23"},
            files={"file": ("x.mp3", b"not audio")},
        ).status_code
        == 422
    )


def test_grounding_discards_fabricated_details(meeting):
    p = Protocol.model_validate(
        {
            "tasks": [
                {
                    "title": "Send report",
                    "owner": "Unknown",
                    "deadline_text": "в пятницу",
                    "due_date": "2026-09-25",
                    "source_ids": [999],
                    "evidence": "invented",
                }
            ]
        }
    )
    result, warnings = ground_protocol(p, meeting)
    t = result["tasks"][0]
    assert t["owner"] is None and t["due_date"] is None and t["source_ids"] == []
    assert t["evidence"] == "" and warnings


def test_grounding_keeps_exact_evidence(meeting):
    p = parse_result(
        "```json\n"
        + json.dumps(
            {
                "summary": "Отчёт",
                "tasks": [
                    {
                        "title": "Подготовить отчёт",
                        "owner": "Айдана",
                        "deadline_text": "завтра",
                        "source_ids": [0],
                        "evidence": "Айдана, подготовь отчёт завтра.",
                    }
                ],
            }
        )
        + "\n```"
    )
    result, _ = ground_protocol(p, meeting)
    assert result["tasks"][0]["owner"] == "Айдана"
    assert result["tasks"][0]["deadline_text"] == "завтра"
    assert result["tasks"][0]["needs_review"]
    assert "<transcript>" in make_prompt(meeting)


def test_review_gate_and_exports(client, meeting):
    p = {
        "summary": "Обсудили отчёт.",
        "tasks": [
            {"title": "Подготовить отчёт", "owner": "Айдана", "needs_review": True}
        ],
        "approved": True,
    }
    assert client.put("/api/meetings/test/protocol", json=p).status_code == 422
    p["tasks"][0]["needs_review"] = False
    assert client.put("/api/meetings/test/protocol", json=p).status_code == 200
    pdf = client.get("/api/meetings/test/export/pdf")
    assert pdf.status_code == 200
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Ә Ғ Қ Ң Ө Ұ Ү Һ І" in text and "Айдана" in text
    doc = Document(io.BytesIO(client.get("/api/meetings/test/export/docx").content))
    assert "Подготовить отчёт" in "\n".join(p.text for p in doc.paragraphs)


def test_edit_invalidates_protocol(client, meeting):
    meeting["protocol"] = {
        "approved": True,
        "summary": "old",
        "tasks": [],
        "decisions": [],
    }
    store.save(meeting)
    response = client.put(
        "/api/meetings/test/transcript",
        json={"segments": meeting["segments"], "speakers": meeting["speakers"]},
    )
    assert response.status_code == 200 and response.json()["protocol"] is None


def test_edit_preserves_only_matching_server_word_timings(client, meeting):
    meeting["segments"][0]["words"] = [dict(start=0, end=4, word=meeting["segments"][0]["text"])]
    store.save(meeting)
    payload = {"segments": meeting["segments"], "speakers": meeting["speakers"]}
    result = client.put("/api/meetings/test/transcript", json=payload).json()
    assert result["segments"][0]["words"] == meeting["segments"][0]["words"]
    payload["segments"][0]["text"] = "Отчёт дайын."
    result = client.put("/api/meetings/test/transcript", json=payload).json()
    assert result["segments"][0]["words"] == []


def test_busy_edits_and_origin_blocked(client, meeting):
    meeting["status"] = "processing"
    store.save(meeting)
    assert client.delete("/api/meetings/test").status_code == 409
    assert (
        client.delete(
            "/api/meetings/test", headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )


def test_bad_timestamps_and_duplicate_ids(client, meeting):
    s = meeting["segments"][0]
    assert (
        client.put(
            "/api/meetings/test/transcript", json={"segments": [s, s], "speakers": {}}
        ).status_code
        == 422
    )
    s["end"] = 999
    assert (
        client.put(
            "/api/meetings/test/transcript", json={"segments": [s], "speakers": {}}
        ).status_code
        == 422
    )


def test_upload_real_wav(client):
    import wave

    audio = io.BytesIO()
    with wave.open(audio, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\0\0" * 16000)
    response = client.post(
        "/api/meetings",
        data={"title": "Synthetic upload check", "meeting_date": "2026-09-23"},
        files={"file": ("silence.wav", audio.getvalue(), "audio/wav")},
    )
    assert response.status_code == 201
    ident = response.json()["id"]
    assert response.json()["duration"] == 1
    assert client.get(f"/api/meetings/{ident}/audio").status_code == 200
    assert client.delete(f"/api/meetings/{ident}").status_code == 204
