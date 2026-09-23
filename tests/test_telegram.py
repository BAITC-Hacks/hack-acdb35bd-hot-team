import time
import pytest
from app import store, telegram_bot as tg


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', tmp_path / 'test.sqlite3')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-only')
    store.init()
    tg.init()
    sent = []
    def fake(method, body=None):
        if method == 'getMe':
            return {'username': 'test_bot'}
        sent.append(body)
        return {'message_id': 1}
    monkeypatch.setattr(tg, 'call', fake)
    m = {'id': 'm1', 'created_at': '', 'protocol': {'approved': True, 'tasks': [
        {'title': 'Подготовить отчёт', 'owner': 'Айдана', 'due_date': '2026-09-25', 'needs_review': False, 'status': 'open'}]}}
    store.save(m)
    return m, sent


def bind(m, chat=42):
    url = tg.invite(m['id'], 'Айдана')['url']
    code = url.split('start=')[1]
    tg.handle({'message': {'chat': {'id': chat, 'type': 'private'}, 'from': {'id': chat}, 'text': '/start '+code}})
    return code


def test_single_use_binding_and_delivery_dedup(bot):
    m, sent = bot
    code = bind(m)
    tg.handle({'message': {'chat': {'id': 99, 'type': 'private'}, 'from': {'id': 99}, 'text': '/start '+code}})
    assert tg.notify(m)['results'][0]['state'] == 'sent'
    assert sent[-1]['chat_id'] == 42
    assert '2026-09-25' in sent[-1]['text']
    assert tg.notify(m)['results'][0]['state'] == 'already_sent'


def test_draft_unbound_done_and_stop(bot):
    m, sent = bot
    assert tg.notify(m)['results'][0]['state'] == 'not_connected'
    bind(m)
    m['protocol']['approved'] = False
    with pytest.raises(tg.BotError):
        tg.notify(m)
    m['protocol']['approved'] = True
    m['protocol']['tasks'][0]['status'] = 'done'
    assert tg.notify(m)['results'] == []
    tg.handle({'message': {'chat': {'id': 42, 'type': 'private'}, 'from': {'id': 42}, 'text': '/stop'}})
    assert tg.links('m1') == []


def test_timeout_is_not_retried_and_expired_link_rejected(bot, monkeypatch):
    m, sent = bot
    code = bind(m)
    attempts = []
    def fail(*args):
        attempts.append(1)
        raise tg.BotError('timeout')
    monkeypatch.setattr(tg, 'call', fail)
    assert tg.notify(m)['results'][0]['state'] == 'unknown'
    assert tg.notify(m)['results'][0]['state'] == 'unknown'
    assert len(attempts) == 1


def test_expired_and_group_links(bot):
    m, sent = bot
    code = tg.invite('m1', 'Айдана')['url'].split('start=')[1]
    tg.handle({'message': {'chat': {'id': 42, 'type': 'group'}, 'from': {'id': 42}, 'text': '/start '+code}})
    assert not tg.links('m1')[0]['connected']
    with store.connect() as con:
        con.execute('UPDATE tg_links SET expires=?', (time.time()-1,))
    tg.handle({'message': {'chat': {'id': 42, 'type': 'private'}, 'from': {'id': 42}, 'text': '/start '+code}})
    assert not tg.links('m1')[0]['connected']


def action(bot):
    m, sent = bot
    bind(m)
    tg.notify(m)
    return sent[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data'].split(':')[1]


def click(key, command, user=42, query_id='q1'):
    tg.handle({'callback_query': {'id': query_id, 'from': {'id': user}, 'data': command+':'+key,
        'message': {'message_id': 1, 'chat': {'id': user, 'type': 'private'}}}})


def test_complete_authorization_and_repeat(bot):
    key = action(bot)
    click(key, 'done', user=99)
    assert store.get('m1')['protocol']['tasks'][0]['status'] == 'open'
    click(key, 'done')
    assert store.get('m1')['protocol']['tasks'][0]['status'] == 'done'
    click(key, 'done', query_id='q2')
    assert store.get('m1')['protocol']['tasks'][0]['status'] == 'done'


def test_reminder_persists_fires_once_and_done_cancels(bot, monkeypatch):
    m, sent = bot
    key = action(bot)
    monkeypatch.setattr(tg.time, 'time', lambda: 1000)
    click(key, 's15')
    tg.init()
    tg.reminders(1899)
    before = len(sent)
    tg.reminders(1900)
    assert len(sent) == before+1
    assert 'Напоминание' in sent[-1]['text']
    tg.reminders(2000)
    assert len(sent) == before+1
    click(key, 's60', query_id='q2')
    click(key, 'done', query_id='q3')
    before = len(sent)
    tg.reminders(99999)
    assert len(sent) == before


def test_edited_or_unlinked_task_rejects_old_buttons(bot):
    key = action(bot)
    m = store.get('m1'); m['protocol']['tasks'][0]['title'] = 'Changed'
    store.save(m)
    click(key, 'done')
    assert store.get('m1')['protocol']['tasks'][0]['status'] == 'open'


def test_replayed_snooze_does_not_shift_time(bot, monkeypatch):
    key = action(bot)
    monkeypatch.setattr(tg.time, 'time', lambda: 1000)
    click(key, 's15')
    monkeypatch.setattr(tg.time, 'time', lambda: 1500)
    click(key, 's15')
    with store.connect() as con:
        assert con.execute('SELECT due FROM tg_actions WHERE key=?', (key,)).fetchone()[0] == 1900
