"""Local exports following the supplied meeting-protocol document layout."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).parent
BLUE = '2E74B5'
HEADERS = ('Поручение', 'Ответственный', 'Срок')


def contents(m):
    p = m.get('protocol') or {}
    yield 'title', 'Протокол совещания'
    if m.get('organization'):
        yield 'organization', m['organization']
    yield 'topic', 'Тема: ' + m['title']
    state = 'Подтверждён пользователем' if p.get('approved') else 'Черновик — требуется проверка'
    yield 'metadata', f"Дата: {m['meeting_date']} · {state}"
    yield 'heading', 'Текст совещания'
    for segment in m.get('segments', []):
        name = m.get('speakers', {}).get(segment['speaker'], segment['speaker'])
        yield 'speaker', name
        yield 'speech', segment['text']
    if not m.get('segments'):
        yield 'speech', 'Текст совещания не подготовлен.'
    yield 'summary', 'Саммари по ключевым пунктам'
    topics = p.get('topics') or []
    if topics:
        for i, topic in enumerate(topics, 1):
            yield 'subheading', f"Тема {i} — {topic['title']}"
            yield 'speech', topic.get('summary') or 'Саммари темы требует уточнения.'
            tasks = [t for t in p.get('tasks', []) if t.get('topic_id') == topic['id']]
            yield 'table', list(task_rows(m, tasks))
        ungrouped = [t for t in p.get('tasks', []) if t.get('topic_id') not in {topic['id'] for topic in topics}]
        if ungrouped:
            yield 'subheading', 'Поручения без темы'
            yield 'table', list(task_rows(m, ungrouped))
    else:
        yield 'speech', p.get('summary') or 'Саммари не подготовлено.'
        yield 'subheading', 'Поручения'
        yield 'table', list(task_rows(m))
    if p.get('decisions'):
        yield 'subheading', 'Решения'
        for decision in p['decisions']:
            yield 'speech', '• ' + decision


def task_rows(m, tasks=None):
    for task in ((m.get('protocol') or {}).get('tasks', []) if tasks is None else tasks):
        owner = task.get('owner') or 'Не указан'
        deadline = task.get('due_date') or task.get('deadline_text') or 'Не указан'
        if task.get('needs_review'):
            owner += '\nТребуется проверка'
        yield [task['title'], owner, deadline]


def docx_bytes(m):
    from docx import Document
    from docx.shared import Pt, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document(ROOT / 'templates/protocol.docx')
    prototypes = dict(zip(('title', 'organization', 'topic', 'heading', 'speaker', 'speech'),
                          [deepcopy(p._p) for p in doc.paragraphs]))
    for p in list(doc.paragraphs):
        p._p.getparent().remove(p._p)
    for kind, text in contents(m):
        if kind == 'table':
            _docx_table(doc, text)
            continue
        prototype = prototypes.get(kind)
        if prototype is not None:
            doc._element.body.insert(len(doc._element.body) - 1, deepcopy(prototype))
            paragraph = doc.paragraphs[-1]
        else:
            paragraph = doc.add_paragraph()
        run = paragraph.add_run(text)
        pf = paragraph.paragraph_format
        if kind in ('heading', 'summary', 'subheading'):
            paragraph.style = 'Heading 2' if kind == 'subheading' else 'Heading 1'
            pf.keep_with_next = True
            if kind == 'summary':
                pf.page_break_before = True
                pf.space_before = Pt(25)
        elif kind == 'title':
            paragraph.style = 'Title'
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif kind in ('organization', 'topic', 'metadata'):
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run.italic = kind == 'organization'
            run.bold = kind == 'topic'
            if kind == 'metadata':
                run.font.size = Pt(10)
                pf.space_after = Pt(10)
        elif kind == 'speaker':
            run.bold = True
            pf.space_before, pf.space_after = Pt(10), Pt(3)
            pf.keep_with_next = True
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def pdf_bytes(m):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, PageBreak, Table, TableStyle
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.colors import HexColor, black

    faces = {'Serif': 'LiberationSerif-Regular', 'Bold': 'LiberationSerif-Bold',
             'Italic': 'LiberationSerif-Italic', 'Sans': 'LiberationSans-Regular'}
    for alias, filename in faces.items():
        name = 'Protocol' + alias
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(ROOT / 'fonts' / (filename + '.ttf'))))
    base = ParagraphStyle('base', fontName='ProtocolSerif', fontSize=12, leading=13.8, spaceAfter=6)
    specs = {
        'title': dict(fontName='ProtocolSans', fontSize=28, leading=33, alignment=1, spaceAfter=5),
        'organization': dict(fontName='ProtocolItalic', alignment=1, spaceAfter=3),
        'topic': dict(fontName='ProtocolBold', alignment=1, spaceAfter=15),
        'metadata': dict(fontSize=10, leading=12, alignment=1, spaceAfter=10),
        'heading': dict(fontName='ProtocolSans', fontSize=16, leading=19, textColor=HexColor('#'+BLUE), spaceBefore=20, spaceAfter=10),
        'summary': dict(fontName='ProtocolSans', fontSize=16, leading=19, textColor=HexColor('#'+BLUE), spaceBefore=25, spaceAfter=10),
        'subheading': dict(fontName='ProtocolSans', fontSize=13, leading=16, textColor=HexColor('#'+BLUE), spaceBefore=10, spaceAfter=6),
        'speaker': dict(fontName='ProtocolBold', spaceBefore=10, spaceAfter=3),
        'speech': {},
    }
    styles = {kind: ParagraphStyle(kind, parent=base, keepWithNext=kind != 'speech', **kw)
              for kind, kw in specs.items()}
    def paragraph(text, style):
        return Paragraph(escape(text).replace('\n', '<br/>'), style)
    story = []
    for kind, text in contents(m):
        if kind == 'table':
            story.append(_pdf_table(text, base, paragraph))
            continue
        if kind == 'summary':
            story.append(PageBreak())
        story.append(paragraph(text, styles[kind]))
    buffer = BytesIO()
    SimpleDocTemplate(buffer, pagesize=LETTER, rightMargin=72, leftMargin=72,
                      topMargin=72, bottomMargin=72, title='Протокол совещания').build(story)
    return buffer.getvalue()


def _docx_table(doc, rows):
    from docx.shared import Pt, Inches
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    if rows:
        table = doc.add_table(rows=1, cols=3)
        table.autofit = False
        for column, width in zip(table.columns, (2.925, 1.95, 1.625)):
            column.width = Inches(width)
        borders = OxmlElement('w:tblBorders')
        for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            el = OxmlElement('w:' + edge)
            for key, value in [('val', 'single'), ('sz', '4'), ('color', '000000')]:
                el.set(qn('w:' + key), value)
            borders.append(el)
        table._tbl.tblPr.append(borders)
        table.rows[0]._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
        for index, values in enumerate([HEADERS] + rows):
            row = table.rows[0] if index == 0 else table.add_row()
            for cell, value, width in zip(row.cells, values, (2.925, 1.95, 1.625)):
                cell.width = Inches(width)
                cell.text = value
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_after = Pt(0)
                    for run in paragraph.runs:
                        run.bold = index == 0
                if index == 0:
                    shade = OxmlElement('w:shd')
                    shade.set(qn('w:fill'), 'D9E2F3')
                    cell._tc.get_or_add_tcPr().append(shade)
    else:
        doc.add_paragraph('Поручения не зафиксированы.')


def _pdf_table(rows, base, paragraph):
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.colors import HexColor, black
    if rows:
        header = ParagraphStyle('header', parent=base, fontName='ProtocolBold', spaceAfter=0)
        cells = [[paragraph(value, header if i == 0 else base) for value in row]
                 for i, row in enumerate([HEADERS] + rows)]
        table = Table(cells, colWidths=[210.6, 140.4, 117], repeatRows=1, splitInRow=1)
        table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), .5, black),
            ('BACKGROUND', (0, 0), (-1, 0), HexColor('#D9E2F3')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5.4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5.4),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))
        return table
    else:
        return paragraph('Поручения не зафиксированы.', base)
