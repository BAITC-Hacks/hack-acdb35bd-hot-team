from tests.test_app import client, meeting
from app import store
from app.schemas import Protocol


def approved(meeting):
    meeting['protocol'] = Protocol.model_validate(dict(approved=True,tasks=[dict(title='Отправить отчёт',needs_review=False)])).model_dump(mode='json')
    return store.save(meeting)


def test_board_move_preserves_protocol_and_detects_stale_state(client, meeting):
    m=approved(meeting);t=m['protocol']['tasks'][0]
    rows=client.get('/api/tasks').json()
    assert rows[0]['id']==t['id'] and rows[0]['meeting_title']==m['title']
    path=f"/api/meetings/test/tasks/{t['id']}"
    assert client.patch(path,json=dict(status='in_progress',expected_status='open')).status_code==200
    assert store.get('test')['protocol']['approved']
    assert client.patch(path,json=dict(status='done',expected_status='open')).status_code==409
    assert client.patch(path,json=dict(status='done',expected_status='in_progress')).status_code==200
    assert client.get('/api/tasks').json()[0]['status']=='done'
    assert client.patch(path,json=dict(status='open',expected_status='done')).status_code==200


def test_draft_busy_missing_and_invalid_moves(client, meeting):
    m=approved(meeting);t=m['protocol']['tasks'][0];path=f"/api/meetings/test/tasks/{t['id']}"
    m['protocol']['approved']=False;store.save(m)
    assert client.patch(path,json=dict(status='done',expected_status='open')).status_code==409
    m['protocol']['approved']=True;m['status']='processing';store.save(m)
    assert client.patch(path,json=dict(status='done',expected_status='open')).status_code==409
    m['status']='ready';store.save(m)
    assert client.patch(path,json=dict(status='invalid',expected_status='open')).status_code==422
    assert client.patch('/api/meetings/test/tasks/missing',json=dict(status='done',expected_status='open')).status_code==404


def test_existing_tasks_get_stable_ids(client, meeting):
    m=approved(meeting);ident=m['protocol']['tasks'][0].pop('id')
    store.save(m)
    first=store.get('test')['protocol']['tasks'][0]['id']
    store.init()
    assert store.get('test')['protocol']['tasks'][0]['id']==first


def test_board_completion_cancels_telegram_reminders(client, meeting):
    from app.telegram_bot import task_signature
    m=approved(meeting);t=m['protocol']['tasks'][0]
    with store.connect() as con:
        con.execute('INSERT INTO tg_actions VALUES (?,?,?,?,?,?)',('test',m['id'],task_signature(t),1,'Test',9999999999))
    r=client.patch(f"/api/meetings/test/tasks/{t['id']}",json=dict(status='done',expected_status='open'))
    assert r.status_code==200
    with store.connect() as con:
        assert con.execute('SELECT due FROM tg_actions WHERE key=?',('test',)).fetchone()[0] is None
