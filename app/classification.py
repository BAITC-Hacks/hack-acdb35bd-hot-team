"""Explainable local suggestions; categories do not assert semantic certainty."""
import re
from datetime import date

PRIORITIES = {'unspecified': 'Не определена', 'normal': 'Обычная', 'high': 'Высокая'}
DIRECTIONS = {'other': 'Не определено', 'finance': 'Финансы', 'procurement': 'Закупки',
              'legal': 'Юридические вопросы', 'safety': 'Безопасность',
              'operations': 'Производство', 'it': 'ИТ', 'hr': 'Персонал'}
RULES = {
    'finance': r'финанс|бюджет|оплат|қаржы|төлем|смет',
    'procurement': r'закуп|поставк|снабжен|сатып ал|жеткізу|сырь',
    'legal': r'юрид|договор|контракт|штраф|келісімшарт|заң',
    'safety': r'безопас|инструктаж|газоопас|қауіпсіз|охран[аы] труда',
    'operations': r'производ|завод|оборудован|модернизац|өндіріс|жабдық',
    'it': r'сервер|программ|сайт|дерекқор|бағдарлама|\bит\b|\bit\b',
    'hr': r'персонал|кадр|сотрудник|қызметкер|обучен|оқыту',
}


def classify(task, today=None):
    text = ' '.join(str(task.get(k) or '') for k in ('title', 'evidence', 'deadline_text')).lower()
    # Don't turn explicit "not urgent" into an urgent task.
    urgent_text = re.sub(r'не\s+срочно|не\s+срочный|срочно\s+не\s+нужно|шұғыл\s+емес|жедел\s+емес', '', text)
    urgent = re.search(r'\b(срочно|срочный|срочная|немедленно|шұғыл|жедел)\b', urgent_text)
    priority, reason = 'unspecified', 'Явная срочность и близкая подтверждённая дата не найдены.'
    if urgent:
        priority, reason = 'high', f'Маркер срочности: «{urgent.group()}». Проверьте контекст.'
    elif re.search(r'не\s+срочно|шұғыл\s+емес|жедел\s+емес', text):
        priority, reason = 'normal', 'В тексте указано, что поручение не срочное.'
    if task.get('due_date'):
        try:
            deadline = date.fromisoformat(str(task['due_date']))
            if (deadline - (today or date.today())).days <= 1:
                priority, reason = 'high', 'Подтверждённый срок просрочен, наступил сегодня или наступит завтра.'
        except ValueError:
            pass
    hits = [key for key, pattern in RULES.items() if re.search(pattern, text)]
    direction = hits[0] if len(hits) == 1 else 'other'
    detail = ('Ключевые слова: ' + DIRECTIONS[direction]) if len(hits) == 1 else (
        'Несколько направлений: ' + ', '.join(DIRECTIONS[k] for k in hits) if hits else 'Нет однозначных ключевых слов.')
    return dict(priority=priority, direction=direction, classification_reason=reason + ' ' + detail)
