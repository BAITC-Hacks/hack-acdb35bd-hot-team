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
    assert result["tasks"] == [] and warnings


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
    assert "Подготовить отчёт" in doc.tables[0].cell(1, 0).text
    assert [c.text for c in doc.tables[0].rows[0].cells] == ["Поручение", "Ответственный", "Срок"]
    assert doc.paragraphs[0].text == "Протокол совещания"
    assert doc.sections[0].page_width.inches == 8.5
    summary = next(p for p in doc.paragraphs if p.text == "Саммари по ключевым пунктам")
    assert summary.paragraph_format.page_break_before


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


def test_protocol_sources_survive_confirmation_but_not_rewriting(client, meeting):
    source = dict(text="Обсудили отчёт.", source_ids=[0], evidence="отчёт")
    meeting['protocol'] = dict(summary=source['text'], decisions=[], tasks=[], approved=False,
                               sources=dict(summary=[source], decisions=[]))
    store.save(meeting)
    payload = dict(meeting['protocol'], approved=True)
    r = client.put('/api/meetings/test/protocol', json=payload)
    assert r.status_code == 200
    assert r.json()['protocol']['sources']['summary'] == [source]
    payload['summary'] = 'Ручное исправление'
    r = client.put('/api/meetings/test/protocol', json=payload)
    assert r.json()['protocol']['sources']['summary'] == []


def test_task_context_survives_save_and_rejects_missing_row(client, meeting):
    task = dict(title='Подготовить отчёт', owner='Айдана', source_ids=[0],
                evidence=meeting['segments'][0]['text'],
                context_evidence=[dict(source_id=0, text='Айдана')])
    response = client.put('/api/meetings/test/protocol', json=dict(tasks=[task]))
    assert response.status_code == 200
    assert response.json()['protocol']['tasks'][0]['context_evidence'] == task['context_evidence']
    task['context_evidence'][0]['source_id'] = 99
    assert client.put('/api/meetings/test/protocol', json=dict(tasks=[task])).status_code == 422


def test_delete_removes_recording_tasks_and_telegram_reminders(client, meeting, tmp_path, monkeypatch):
    import importlib
    main = importlib.import_module('app.main')
    monkeypatch.setattr(main, 'DATA', tmp_path)
    folder = tmp_path / meeting['id']
    folder.mkdir()
    (folder / 'audio.wav').write_bytes(b'test-only')
    meeting['protocol'] = Protocol(tasks=[dict(title='Тестовое поручение')]).model_dump(mode='json')
    store.save(meeting)
    with store.connect() as con:
        con.execute('INSERT INTO tg_links VALUES (?,?,?,?,?)', ('test','Тест','hash',0,1))
        con.execute('INSERT INTO tg_actions VALUES (?,?,?,?,?,?)', ('key','test','signature',1,'Тест',1))
    assert client.delete('/api/meetings/test').status_code == 204
    assert not folder.exists()
    assert client.get('/api/meetings/test').status_code == 404
    assert not client.get('/api/tasks').json()
    with store.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM tg_actions WHERE meeting=?', ('test',)).fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM tg_links WHERE meeting=?', ('test',)).fetchone()[0] == 0
    assert client.delete('/api/meetings/test').status_code == 404


def test_protocol_export_long_table_and_unknown_owner():
    from app.export import docx_bytes, pdf_bytes
    m = dict(title='Тест <план> & отчёт', meeting_date='2026-09-23', segments=[],
             protocol=dict(summary='Ә Ғ Қ Ң Ө Ұ Ү Һ І', tasks=[
                 dict(title=('Проверить показатели и подготовить отчёт. ' * 180), needs_review=True)]))
    pdf = PdfReader(io.BytesIO(pdf_bytes(m)))
    text = '\n'.join(page.extract_text() for page in pdf.pages)
    assert len(pdf.pages) >= 3
    assert 'Тест <план> & отчёт' in text
    assert 'Требуется проверка' in text
    doc = Document(io.BytesIO(docx_bytes(m)))
    assert doc.tables[0].cell(1, 1).text == 'Не указан\nТребуется проверка'
    assert doc.tables[0].cell(1, 2).text == 'Не указан'
    assert 'Черновик' in '\n'.join(p.text for p in doc.paragraphs)


def test_topic_edits_roundtrip_and_bad_source(client, meeting):
    protocol = dict(topics=[dict(id='topic-1', title='Планирование', summary='Обсудили отчёт.', source_ids=[0])],
                    summary='Обсудили отчёт.', tasks=[dict(title='Подготовить отчёт', topic_id='topic-1')])
    assert client.put('/api/meetings/test/protocol', json=protocol).status_code == 200
    saved = client.get('/api/meetings/test').json()['protocol']
    assert saved['topics'][0]['title'] == 'Планирование'
    assert client.get('/api/tasks').json()[0]['topic_title'] == 'Планирование'
    saved['topics'][0]['title'] = 'Обновлённая тема'
    assert client.put('/api/meetings/test/protocol', json=saved).status_code == 200
    assert client.get('/api/tasks').json()[0]['topic_title'] == 'Обновлённая тема'
    saved['topics'][0]['source_ids'] = [9999]
    assert client.put('/api/meetings/test/protocol', json=saved).status_code == 422
