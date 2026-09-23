import importlib
import pytest
from app import store
from app.progress import Progress
from tests.test_app import client, meeting

main = importlib.import_module('app.main')


def test_combined_progress_spans_both_steps(client, meeting):
    first = Progress(meeting, 'transcribe', combined=True)
    first.update(100, 'Готово', force=True)
    assert meeting['progress']['stage'] == 'prepare'
    assert meeting['progress']['percent'] == 70
    started = meeting['progress']['started_at']
    second = Progress(meeting, 'diarize', combined=True)
    second.update(50, 'Голоса', force=True)
    assert meeting['progress']['percent'] == 85
    assert meeting['progress']['started_at'] == started
    second.complete()
    assert meeting['progress']['percent'] == 100


@pytest.mark.parametrize('fail', [None, 'transcribe', 'diarize'])
def test_chain_uses_separate_processes_and_stops_on_failure(client, meeting, monkeypatch, fail):
    calls = []
    class Process:
        def __init__(self, command, **kwargs):
            self.step = command[4]
            assert command[-1] == '--combined'
            calls.append(self.step)
        def wait(self, timeout):
            m = store.get(meeting['id'])
            m['status'] = 'error' if self.step == fail else ('processing' if self.step == 'transcribe' else 'transcribed')
            store.save(m)
            return 0
    monkeypatch.setattr(main.subprocess, 'Popen', Process)
    main.run_job(meeting['id'], 'prepare')
    main.active_process = None
    assert calls == (['transcribe'] if fail == 'transcribe' else ['transcribe', 'diarize'])
    assert store.get(meeting['id'])['status'] == ('error' if fail else 'transcribed')


def test_prepare_checks_both_models_before_start(client, meeting, monkeypatch):
    checked = []
    def available(repo):
        checked.append(repo)
        return repo != main.DIAR_MODEL
    monkeypatch.setattr(main, 'available', available)
    response = client.post('/api/meetings/test/process', json={'stage':'prepare'})
    assert response.status_code == 409
    assert checked == [main.ASR_MODEL, main.DIAR_MODEL]
    assert store.get('test')['status'] == 'transcribed'
