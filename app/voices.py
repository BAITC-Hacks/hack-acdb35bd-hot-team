"""Local opt-in voice profiles. Scores are similarity, never identity probability."""
import json
import math
import uuid
from datetime import datetime, timezone
from . import store
from .config import DATA, DIAR_MODEL

MODEL = DIAR_MODEL + ':pyannote4-centroid'
THRESHOLD = .8
MARGIN = .1
MIN_SECONDS = 10


def init():
    with store.connect() as con:
        con.execute('CREATE TABLE IF NOT EXISTS voice_profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL, model TEXT NOT NULL, embedding TEXT NOT NULL, created_at TEXT NOT NULL)')


def normalized(values):
    if not values or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in values):
        raise ValueError('Непригодный голосовой образец')
    norm = math.sqrt(sum(x*x for x in values))
    if norm < 1e-8:
        raise ValueError('Пустой голосовой образец')
    return [x/norm for x in values]


def clean_durations(turns):
    events = []
    for start, end, label in turns:
        events.extend([(start, 1, label), (end, -1, label)])
    active, durations, previous = {}, {}, 0
    for instant, delta, label in sorted(events):
        if sum(active.values()) == 1:
            speaker = next(k for k, count in active.items() if count)
            durations[speaker] = durations.get(speaker, 0) + instant - previous
        active[label] = active.get(label, 0) + delta
        previous = instant
    return durations


def samples(ident):
    path = DATA / ident / 'voice-samples.json'
    return json.loads(path.read_text()) if path.exists() else {}


def save_samples(ident, labels, embeddings, turns):
    durations = clean_durations(turns)
    result = {}
    if embeddings is not None:
        for label, vector in zip(labels, embeddings):
            try:
                result[label] = dict(embedding=normalized(vector.tolist()), seconds=durations.get(label, 0), model=MODEL)
            except ValueError:
                continue
    path = DATA / ident / 'voice-samples.json'
    path.write_text(json.dumps(result))
    path.chmod(0o600)


def profiles():
    init()
    with store.connect() as con:
        return [dict(id=r[0], name=r[1], created_at=r[2]) for r in con.execute('SELECT id,name,created_at FROM voice_profiles ORDER BY name')]


def enroll(ident, speaker, name, consent):
    if not consent:
        raise ValueError('Подтвердите разрешение участника на сохранение голосового профиля.')
    name = name.strip()
    if not name or name.startswith(('SPEAKER_', 'Спикер ', 'Участник не определён')):
        raise ValueError('Укажите настоящее имя или согласованный псевдоним участника.')
    sample = samples(ident).get(speaker)
    if not sample or sample['seconds'] < MIN_SECONDS:
        raise ValueError('Нужно не менее 10 секунд речи без одновременных голосов. Повторите разделение голосов или используйте более длинную запись.')
    vector = normalized(sample['embedding'])
    init()
    with store.connect() as con:
        if con.execute('SELECT 1 FROM voice_profiles WHERE name=? COLLATE NOCASE', (name,)).fetchone():
            raise ValueError('Профиль с таким именем уже существует. Удалите старый профиль для замены.')
        ident = uuid.uuid4().hex
        con.execute('INSERT INTO voice_profiles VALUES (?,?,?,?,?)', (ident, name, sample['model'], json.dumps(vector), datetime.now(timezone.utc).isoformat()))
    return dict(id=ident, name=name)


def candidates(ident):
    init()
    with store.connect() as con:
        rows = con.execute('SELECT id,name,model,embedding FROM voice_profiles').fetchall()
    output = {}
    for speaker, sample in samples(ident).items():
        scores = []
        if sample['seconds'] >= MIN_SECONDS:
            vector = normalized(sample['embedding'])
            for profile_id, name, model, raw in rows:
                other = normalized(json.loads(raw))
                if model == sample['model'] and len(vector) == len(other):
                    scores.append((sum(a*b for a,b in zip(vector,other)), profile_id, name))
        scores.sort(reverse=True)
        match = scores and scores[0][0] >= THRESHOLD and (len(scores) == 1 or scores[0][0]-scores[1][0] >= MARGIN)
        output[speaker] = dict(seconds=round(sample['seconds'], 1), name=scores[0][2] if match else None,
                               score=round(scores[0][0], 3) if match else None)
    # Do not assign one saved identity to multiple diarized speakers.
    names = [v['name'] for v in output.values() if v['name']]
    for value in output.values():
        if value['name'] and names.count(value['name']) > 1:
            value.update(name=None, score=None)
    return output


def delete(ident):
    init()
    with store.connect() as con:
        return con.execute('DELETE FROM voice_profiles WHERE id=?', (ident,)).rowcount > 0
