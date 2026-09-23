from app.analysis import parse_result, ground_protocol, make_prompt
import json


def meeting():
    return dict(meeting_date="2026-09-23", participants=["Айдана", "Асет"],
                speakers={"A": "Асет", "B": "Айдана"}, segments=[
                    dict(id=0, speaker="A", text="Айдана, отчётты жұма күні жібер.", uncertain=True),
                    dict(id=1, speaker="B", text="Жақсы, жіберемін.", uncertain=False),
                    dict(id=2, speaker="A", text="Решили перенести встречу.", uncertain=False)])


def test_facts_need_exact_single_row_quotes_and_deduplicate():
    fact = dict(text="Встречу перенесли.", evidence="Решили перенести встречу.", source_ids=[2])
    draft = parse_result(json.dumps(dict(summary_facts=[fact, fact,
        dict(text="Выдуманный факт", evidence="Не было", source_ids=[0])], decision_facts=[fact])))
    result, warnings = ground_protocol(draft, meeting())
    assert result['summary'] == "Встречу перенесли."
    assert result['decisions'] == ["Встречу перенесли."]
    assert result['sources']['summary'][0]['source_ids'] == [2]
    assert warnings and not result['approved']


def test_reject_joined_quotes_and_missing_ids():
    tasks = [dict(title="Отправить отчёт", evidence="жібер. Жақсы", source_ids=[0, 1]),
             dict(title="Отправить отчёт", evidence="Жақсы", source_ids=[1, 99])]
    result, _ = ground_protocol(parse_result(json.dumps(dict(tasks=tasks))), meeting())
    assert result['tasks'] == []


def test_uncertain_tasks_remain_reviewable_and_deadline_must_be_in_quote():
    task = dict(title="Отправить отчёт", evidence="Айдана, отчётты жұма күні жібер.",
                source_ids=[0], owner="Айдана", deadline_text="жұма күні")
    result, warnings = ground_protocol(parse_result(json.dumps(dict(tasks=[task,task]))), meeting())
    assert len(result['tasks']) == 1
    assert result['tasks'][0]['owner'] == 'Айдана'
    assert result['tasks'][0]['deadline_text'] == 'жұма күні'
    assert result['tasks'][0]['needs_review'] and result['tasks'][0]['due_date'] is None
    assert any('сомнительных' in w for w in warnings)
    assert '"uncertain": true' in make_prompt(meeting())


def test_unquoted_summary_and_repetition_are_not_presented_as_facts():
    draft = parse_result(json.dumps(dict(summary="Выдумка", summary_facts=[dict(
        text="Один два три четыре " * 4, evidence="Жақсы", source_ids=[1])], tasks=[])))
    result, warnings = ground_protocol(draft, meeting())
    assert result['summary'] == '' and warnings


def test_looping_transcript_is_not_evidence_for_a_fluent_fact():
    m = meeting()
    m['segments'][0]['text'] = 'қазір есеп дайын болды ' * 5
    draft = parse_result(json.dumps(dict(summary_facts=[dict(
        text='Отчёт готов.', source_ids=[0], evidence='есеп дайын')])))
    result, _ = ground_protocol(draft, m)
    assert result['summary'] == ''


def test_unlisted_person_and_department_are_kept_when_explicitly_assigned():
    m=meeting();m['participants']=[];m['speakers']={'A':'Спикер 1','B':'Спикер 2'}
    m['segments']=[dict(id=0,speaker='A',text='Ерлан, подготовь претензию до пятницы.',uncertain=False),
                   dict(id=1,speaker='A',text='Юридический департамент, проверьте договор.',uncertain=False)]
    tasks=[dict(title='Подготовить претензию',owner='Ерлан',evidence=m['segments'][0]['text'],source_ids=[0]),
           dict(title='Проверить договор',owner='Юридический департамент',evidence=m['segments'][1]['text'],source_ids=[1])]
    result,_=ground_protocol(parse_result(json.dumps(dict(tasks=tasks))),m)
    assert [t['owner'] for t in result['tasks']]==['Ерлан','Юридический департамент']


def test_context_supplies_addressee_and_final_deadline_without_joining_quotes():
    m=meeting();m['participants']=[];m['speakers']={'A':'Спикер 1','B':'Спикер 2'}
    m['segments']=[dict(id=i,speaker='A' if i%2==0 else 'B',text=t,uncertain=False) for i,t in enumerate([
        'Дана, вы курируете подрядчика?', 'Да.', 'Тогда подготовьте претензию.', 'Хорошо, к среде сделаю.'])]
    task=dict(title='Подготовить претензию',owner='Дана',deadline_text='к среде',evidence=m['segments'][2]['text'],source_ids=[2],
              context_evidence=[dict(source_id=0,text=m['segments'][0]['text']),dict(source_id=3,text=m['segments'][3]['text'])])
    result,_=ground_protocol(parse_result(json.dumps(dict(tasks=[task]))),m)
    t=result['tasks'][0]
    assert t['owner']=='Дана' and t['deadline_text']=='к среде'
    assert t['source_ids']==[2,0,3] and len(t['context_evidence'])==2
    task['context_evidence']=[dict(source_id=0,text='Выдуманное обращение к Дане')]
    result,_=ground_protocol(parse_result(json.dumps(dict(tasks=[task]))),m)
    assert result['tasks'][0]['owner'] is None and result['tasks'][0]['deadline_text'] is None


def test_name_match_does_not_accept_part_of_another_name():
    m=meeting();m['segments'][0]['text']='Айдана, отправь отчёт.'
    task=dict(title='Отправить отчёт',owner='Дана',source_ids=[0],evidence=m['segments'][0]['text'])
    result,_=ground_protocol(parse_result(json.dumps(dict(tasks=[task]))),m)
    assert result['tasks'][0]['owner'] is None


def test_self_introduction_not_address_identifies_voice():
    from app.analysis import named_speakers
    m=meeting();m['speakers']={'A':'Спикер 1','B':'Спикер 2'}
    m['segments'][0]['text']='Меня зовут Дана.';m['segments'][0]['uncertain']=False
    m['segments'][1]['text']='Дана, отправьте отчёт.'
    assert named_speakers(m)=={'A':'Дана'}
    m['speakers']['A']='Имя, введённое пользователем'
    assert named_speakers(m)=={}


def test_generic_voice_label_is_not_an_assignee():
    m = meeting()
    m['speakers'] = {'A': 'Спикер 1', 'B': 'Спикер 2'}
    task = dict(title='Отправить отчёт', owner='Спикер 2', source_ids=[1], evidence='Жақсы, жіберемін.')
    result, _ = ground_protocol(parse_result(json.dumps(dict(tasks=[task]))), m)
    assert result['tasks'][0]['owner'] is None
