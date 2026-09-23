import json
from datetime import date
import pytest
from fastapi.testclient import TestClient
from app import store, voices
from app.main import app
from app.classification import classify


def test_classification_is_explainable_and_conservative():
    assert classify({'title': 'Срочно проверить бюджет'})['priority'] == 'high'
    assert classify({'title': 'Срочно проверить бюджет'})['direction'] == 'finance'
    assert classify({'title': 'Не срочно проверить бюджет'})['priority'] == 'normal'
    assert classify({'title': 'Қауіпсіздік бойынша есеп', 'evidence': 'Шұғыл тексеру'})['direction'] == 'safety'
    assert classify({'title': 'Шұғыл емес тапсырма'})['priority'] == 'normal'
    assert classify({'title': 'Проверить договор и бюджет'})['direction'] == 'other'
    assert classify({'title': 'Позвонить'})['priority'] == 'unspecified'
    assert classify({'due_date':'2026-09-24'}, today=date(2026,9,23))['priority'] == 'high'
    assert classify({'due_date':'2026-10-01'}, today=date(2026,9,23))['priority'] == 'unspecified'


@pytest.fixture
def voice_client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', tmp_path/'db.sqlite3')
    monkeypatch.setattr(voices, 'DATA', tmp_path)
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    with TestClient(app) as client:
        m=dict(id='voice-test', title='Synthetic test', meeting_date='2026-09-23',
               created_at='2026-09-23',status='transcribed',diarized=True,
               segments=[dict(id=0,start=0,end=12,text='Проверить бюджет',speaker='SPEAKER_00')],
               speakers={'SPEAKER_00':'Спикер 1'},protocol=None)
        store.save(m)
        folder=tmp_path/'voice-test';folder.mkdir()
        sample={'SPEAKER_00':dict(embedding=[1.,0.,0.],seconds=12,model=voices.MODEL)}
        (folder/'voice-samples.json').write_text(json.dumps(sample))
        yield client,folder,m


def test_profile_consent_delete_and_matching(voice_client):
    client,folder,m=voice_client
    url='/api/meetings/voice-test/voices'
    payload=dict(speaker='SPEAKER_00',name='Тестовый участник',consent=False)
    assert client.post(url,json=payload).status_code==422
    payload['consent']=True
    profile=client.post(url,json=payload).json()
    assert 'id' in profile
    data=client.get(url).json()
    assert data['candidates']['SPEAKER_00']['name']=='Тестовый участник'
    assert 'embedding' not in json.dumps(data)
    assert store.get(m['id'])['speakers']['SPEAKER_00']=='Спикер 1'
    assert client.post(url,json=payload).status_code==422
    # A second equally similar identity must make matching ambiguous.
    payload['name']='Второй участник'
    other=client.post(url,json=payload).json()
    assert client.get(url).json()['candidates']['SPEAKER_00']['name'] is None
    assert client.delete('/api/voices/'+other['id']).status_code==200
    assert client.delete('/api/voices/'+profile['id']).status_code==200
    assert client.get(url).json()['candidates']['SPEAKER_00']['name'] is None
    assert client.get(url).json()['profiles']==[]


def test_short_unknown_and_model_mismatch(voice_client):
    client,folder,m=voice_client
    path=folder/'voice-samples.json';data=json.loads(path.read_text())
    data['SPEAKER_00']['seconds']=3;path.write_text(json.dumps(data))
    payload=dict(speaker='SPEAKER_00',name='Образец',consent=True)
    assert client.post('/api/meetings/voice-test/voices',json=payload).status_code==422
    data['SPEAKER_00']['seconds']=12;path.write_text(json.dumps(data))
    assert client.post('/api/meetings/voice-test/voices',json=payload).status_code==200
    data['SPEAKER_00']['embedding']=[0.,1.,0.];path.write_text(json.dumps(data))
    assert voices.candidates('voice-test')['SPEAKER_00']['name'] is None
    data['SPEAKER_00']['embedding']=[1.,0.,0.];data['SPEAKER_00']['model']='another-model';path.write_text(json.dumps(data))
    assert voices.candidates('voice-test')['SPEAKER_00']['name'] is None
    m['status']='processing';store.save(m)
    assert client.post('/api/meetings/voice-test/voices',json=payload).status_code==409


def test_clean_speech_excludes_overlap_and_bad_vectors():
    assert voices.clean_durations([(0,10,'A'),(5,15,'B')])=={'A':5,'B':5}
    for vector in ([0,0],[float('nan'),1],[float('inf'),1],[]):
        with pytest.raises(ValueError): voices.normalized(vector)


def test_classification_saved_in_protocol_and_board(voice_client):
    client,folder,m=voice_client
    protocol=dict(summary='Итог',tasks=[dict(title='Срочно проверить бюджет',needs_review=False)],approved=True)
    assert client.put('/api/meetings/voice-test/protocol',json=protocol).status_code==200
    result=client.post('/api/meetings/voice-test/classify').json()['protocol']
    assert result['tasks'][0]['priority']=='high'
    assert result['tasks'][0]['direction']=='finance'
    assert not result['approved']
    result['tasks'][0]['direction']='legal'
    assert client.put('/api/meetings/voice-test/protocol',json=result).status_code==200
    assert client.get('/api/tasks').json()[0]['direction']=='legal'
    assert client.get('/api/meetings/voice-test/export/json').json()['protocol']['tasks'][0]['direction']=='legal'
