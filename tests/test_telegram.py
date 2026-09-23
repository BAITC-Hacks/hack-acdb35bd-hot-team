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
