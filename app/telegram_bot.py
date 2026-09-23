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
        CREATE TABLE IF NOT EXISTS tg_actions (key TEXT PRIMARY KEY, meeting TEXT, signature TEXT, chat INTEGER, owner TEXT, due REAL);
        CREATE TABLE IF NOT EXISTS tg_callbacks (id TEXT PRIMARY KEY);
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
    if "callback_query" in update:
        return callback(update["callback_query"])
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
            reminders()
            with store.connect() as con:
                row = con.execute("SELECT value FROM tg_settings WHERE key='offset'").fetchone()
            updates = call('getUpdates', {'offset': int(row[0]) if row else 0,
                                          'timeout': 20, 'allowed_updates': ['message', 'callback_query']})
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
        text = task_text(task)
        fingerprint = hashlib.sha256(json.dumps([m['id'], row[0], text], ensure_ascii=False).encode()).hexdigest()
        with store.connect() as con:
            old = con.execute('SELECT state FROM tg_deliveries WHERE fingerprint=?', (fingerprint,)).fetchone()
            if old:
                results.append({'owner': owner, 'state': 'already_sent' if old[0] == 'sent' else 'unknown'})
                continue
            con.execute("INSERT INTO tg_deliveries VALUES (?, 'unknown')", (fingerprint,))
        key = secrets.token_urlsafe(18)
        with store.connect() as con:
            con.execute('INSERT INTO tg_actions VALUES (?,?,?,?,?,NULL)', (key, m['id'], task_signature(task), row[0], owner))
        try:
            call('sendMessage', {'chat_id': row[0], 'text': text, 'reply_markup': keyboard(key)})
        except BotError:
            results.append({'owner': owner, 'state': 'unknown'})
            continue
        with store.connect() as con:
            con.execute("UPDATE tg_deliveries SET state='sent' WHERE fingerprint=?", (fingerprint,))
        results.append({'owner': owner, 'state': 'sent'})
    return {'results': results}

def task_signature(task):
    return hashlib.sha256(json.dumps({k: task.get(k) for k in
        ('title', 'owner', 'due_date', 'deadline_text', 'source_ids', 'evidence')},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def keyboard(key):
    return {'inline_keyboard': [[{'text': '✅ Выполнено', 'callback_data': 'done:'+key}],
                               [{'text': '⏰ Напомнить позже', 'callback_data': 'later:'+key}]]}


def action_task(row):
    key, meeting_id, signature, chat, owner, due = row
    m = store.get(meeting_id)
    with store.connect() as con:
        link = con.execute('SELECT chat FROM tg_links WHERE meeting=? AND owner=?', (meeting_id, owner)).fetchone()
    if not m or not link or link[0] != chat or m.get('status') in ('queued','processing'):
        return None, None
    p = m.get('protocol') or {}
    matches = [t for t in p.get('tasks', []) if task_signature(t) == signature]
    if not p.get('approved') or len(matches) != 1 or matches[0].get('needs_review', True):
        return None, None
    return m, matches[0]


def callback(query):
    data = query.get('data', '')
    if ':' not in data:
        return
    command, key = data.split(':', 1)
    if command not in ('done', 'later', 's15', 's60', 's1440'):
        return
    message = query.get('message', {})
    chat = message.get('chat', {})
    user = query.get('from', {}).get('id')
    with store.mutation_lock:
        with store.connect() as con:
            row = con.execute('SELECT * FROM tg_actions WHERE key=?', (key,)).fetchone()
        if not row or chat.get('type') != 'private' or chat.get('id') != row[3] or user != row[3]:
            call('answerCallbackQuery', {'callback_query_id': query['id'], 'text': 'Это поручение недоступно.'})
            return
        m, task = action_task(row)
        if task is None:
            reply = 'Поручение изменено или доступ отключён. Обратитесь к организатору.'
            markup = {'inline_keyboard': []}
        elif task.get('status') == 'done':
            reply = 'Поручение уже выполнено.'
            markup = {'inline_keyboard': []}
        elif command == 'done':
            task['status'] = 'done'
            store.save(m)
            with store.connect() as con:
                con.execute('UPDATE tg_actions SET due=NULL WHERE meeting=? AND signature=?', (row[1], row[2]))
            reply = '✅ Поручение отмечено выполненным.'
            markup = {'inline_keyboard': []}
        elif command == 'later':
            reply = 'Когда напомнить?'
            markup = {'inline_keyboard': [[{'text': label, 'callback_data': cmd+':'+key}]
                for label, cmd in [('Через 15 минут','s15'),('Через 1 час','s60'),('Через 24 часа','s1440')]]}
        else:
            minutes = int(command[1:])
            with store.connect() as con:
                # Replayed updates must not postpone the reminder again.
                seen = con.execute('SELECT 1 FROM tg_callbacks WHERE id=?', (query['id'],)).fetchone()
                if not seen:
                    con.execute('UPDATE tg_actions SET due=? WHERE key=?', (time.time()+minutes*60, key))
                    con.execute('INSERT INTO tg_callbacks VALUES (?)', (query['id'],))
            reply = f'⏰ Напомню через {minutes} мин. Приложение должно быть включено.'
            markup = keyboard(key)
        call('answerCallbackQuery', {'callback_query_id': query['id'], 'text': reply, 'show_alert': True})
        call('editMessageReplyMarkup', {'chat_id': row[3], 'message_id': message['message_id'], 'reply_markup': markup})


def reminders(now=None):
    now = time.time() if now is None else now
    with store.mutation_lock:
        with store.connect() as con:
            rows = con.execute('SELECT * FROM tg_actions WHERE due>=0 AND due<=?', (now,)).fetchall()
        for row in rows:
            m, task = action_task(row)
            with store.connect() as con:
                con.execute('UPDATE tg_actions SET due=-1 WHERE key=?', (row[0],))
            if task is None or task.get('status') == 'done':
                with store.connect() as con:
                    con.execute('UPDATE tg_actions SET due=NULL WHERE key=?', (row[0],))
                continue
            # Claim before sending: do not duplicate on ambiguous network failure.
            try:
                call('sendMessage', {'chat_id': row[3], 'text': '⏰ Напоминание\n'+task_text(task), 'reply_markup': keyboard(row[0])})
            except BotError:
                continue
            with store.connect() as con:
                con.execute('UPDATE tg_actions SET due=NULL WHERE key=?', (row[0],))


def task_text(task):
    return f"Поручение\n{task['title']}\nИсполнитель: {task.get('owner')}\nСрок: {task.get('due_date') or task.get('deadline_text') or 'не указан'}"
