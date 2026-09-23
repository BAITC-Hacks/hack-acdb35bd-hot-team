from app.progress import Progress
from app import store
from tests.test_app import client, meeting


def test_progress_is_monotonic_and_does_not_publish_partial_results(client, meeting):
    p = Progress(meeting, 'transcribe')
    meeting['segments'] = []  # worker-local unfinished edit
    p.update(60, 'Аудио', force=True)
    p.update(20, 'Поздний callback', force=True)
    saved = store.get(meeting['id'])
    assert saved['progress']['percent'] == 60
    assert saved['segments']  # original persisted result remains intact
    p.update(100, 'Сохранение', force=True)
    assert store.get(meeting['id'])['progress']['percent'] == 99
    p.complete()
    assert meeting['progress']['percent'] == 100
    assert store.get(meeting['id'])['progress']['percent'] == 99
    store.save(meeting)
    assert store.get(meeting['id'])['progress']['percent'] == 100


def test_error_does_not_become_completed_and_restart_preserves_failure(client, meeting):
    p = Progress(meeting, 'analyze')
    p.update(20, 'Генерация', estimated=True, force=True)
    meeting['status'] = 'processing'
    store.save(meeting)
    store.init()
    saved = store.get(meeting['id'])
    assert saved['status'] == 'error'
    assert saved['progress']['percent'] == 20
