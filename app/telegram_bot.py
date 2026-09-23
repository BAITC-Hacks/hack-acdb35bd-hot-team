"""Optional Telegram delivery; never used by local inference workers."""
import hashlib
import json
import os
import secrets
import threading
import time

import httpx
from . import store


class BotError(Exception):
    pass


def enabled():
    return bool(os.environ.get('TELEGRAM_BOT_TOKEN', '').strip())


def call(method, payload=None):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token:
        raise BotError('Добавьте TELEGRAM_BOT_TOKEN в .env и перезапустите приложение.')
    try:
        response = httpx.post(f'https://api.telegram.org/bot{token}/{method}',
                              json=payload or {}, timeout=35, trust_env=False)
        body = response.json()
        if response.status_code != 200 or not body.get('ok'):
            raise BotError('Telegram отклонил запрос. Проверьте токен, доступность бота и отсутствие другого polling/webhook.')
        return body['result']
    except BotError:
        raise
    except Exception:
        # Exception URLs contain credentials. Never propagate them.
        raise BotError('Нет подтверждения от Telegram. Проверьте интернет и чат получателя.') from None


def init():
    with store.connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS tg_links (
          meeting TEXT, owner TEXT, code_hash TEXT, expires REAL, chat INTEGER,
          PRIMARY KEY(meeting, owner));
        CREATE TABLE IF NOT EXISTS tg_deliveries (
          fingerprint TEXT PRIMARY KEY, state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tg_settings (key TEXT PRIMARY KEY, value TEXT);
        ''')


def invite(ident, owner):
    username = call('getMe')['username']
    code = secrets.token_urlsafe(24)
    digest = hashlib.sha256(code.encode()).hexdigest()
    with store.connect() as con:
        con.execute('INSERT OR REPLACE INTO tg_links VALUES (?,?,?,?,NULL)',
                    (ident, owner, digest, time.time() + 86400))
    return {'url': f'https://t.me/{username}?start={code}', 'expires_in_hours': 24}


def links(ident):
    with store.connect() as con:
        rows = con.execute('SELECT owner, chat FROM tg_links WHERE meeting=?', (ident,)).fetchall()
    return [{'owner': owner, 'connected': chat is not None} for owner, chat in rows]


def handle(update):
    msg = update.get('message', {})
    chat = msg.get('chat', {})
    if chat.get('type') != 'private' or msg.get('from', {}).get('id') != chat.get('id'):
        return
    ident = chat['id']
    text = msg.get('text', '')
    if text == '/stop':
        with store.connect() as con:
            con.execute('DELETE FROM tg_links WHERE chat=?', (ident,))
        call('sendMessage', {'chat_id': ident, 'text': 'Уведомления отключены. Для подключения получите новую ссылку.'})
    elif text.startswith('/start '):
        digest = hashlib.sha256(text.split(' ', 1)[1].strip().encode()).hexdigest()
        with store.connect() as con:
            row = con.execute('SELECT meeting,owner FROM tg_links WHERE code_hash=? AND expires>? AND chat IS NULL',
                              (digest, time.time())).fetchone()
            if row and store.get(row[0]):
                con.execute('UPDATE tg_links SET chat=?,code_hash=NULL WHERE code_hash=?', (ident, digest))
            else:
                row = None
        reply = (f'Подключено: {row[1]}. Здесь будут поручения из выбранного совещания. /stop — отключить.'
                 if row else 'Ссылка недействительна или уже использована. Попросите новую ссылку у организатора.')
        call('sendMessage', {'chat_id': ident, 'text': reply})
    elif text == '/start':
        call('sendMessage', {'chat_id': ident, 'text': 'Попросите у организатора персональную ссылку для подключения поручений. /stop — отключить.'})


def poll(stop):
    while not stop.is_set():
        try:
            with store.connect() as con:
                row = con.execute("SELECT value FROM tg_settings WHERE key='offset'").fetchone()
            updates = call('getUpdates', {'offset': int(row[0]) if row else 0,
                                          'timeout': 20, 'allowed_updates': ['message']})
            for update in updates:
                if stop.is_set():
                    return
                try:
                    handle(update)
                except BotError:
                    pass  # Binding/stop persist even if acknowledgement fails.
                with store.connect() as con:
                    con.execute("INSERT OR REPLACE INTO tg_settings VALUES ('offset',?)", (str(update['update_id'] + 1),))
        except Exception:
            stop.wait(10)  # No token-bearing exception logs and no busy loop.


def notify(m):
    protocol = m.get('protocol') or {}
    if not protocol.get('approved') or any(t.get('needs_review', True) for t in protocol.get('tasks', [])):
        raise BotError('Сначала сохраните и подтвердите проверенный протокол.')
    if not enabled():
        raise BotError('Telegram-бот не настроен.')
    results = []
    for task in protocol.get('tasks', []):
        if task.get('status') == 'done':
            continue
        owner = task.get('owner')
        with store.connect() as con:
            row = con.execute('SELECT chat FROM tg_links WHERE meeting=? AND owner=?', (m['id'], owner)).fetchone()
        if not row or row[0] is None:
            results.append({'owner': owner, 'state': 'not_connected'})
            continue
        text = f"Поручение\n{task['title']}\nИсполнитель: {owner}\nСрок: {task.get('due_date') or task.get('deadline_text') or 'не указан'}"
        fingerprint = hashlib.sha256(json.dumps([m['id'], row[0], text], ensure_ascii=False).encode()).hexdigest()
        with store.connect() as con:
            old = con.execute('SELECT state FROM tg_deliveries WHERE fingerprint=?', (fingerprint,)).fetchone()
            if old:
                results.append({'owner': owner, 'state': 'already_sent' if old[0] == 'sent' else 'unknown'})
                continue
            con.execute("INSERT INTO tg_deliveries VALUES (?, 'unknown')", (fingerprint,))
        try:
            call('sendMessage', {'chat_id': row[0], 'text': text})
        except BotError:
            results.append({'owner': owner, 'state': 'unknown'})
            continue
        with store.connect() as con:
            con.execute("UPDATE tg_deliveries SET state='sent' WHERE fingerprint=?", (fingerprint,))
        results.append({'owner': owner, 'state': 'sent'})
    return {'results': results}
