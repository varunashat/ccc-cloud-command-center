"""Noventiq PPT builder: build a deck on a Noventiq template from a brief and Noventiq source decks.

Library (shared by the team, stored on the mounted share):
  templates  - .potx / .pptx files whose slide layouts are used for the new deck
  sources    - Noventiq decks whose text is the factual source (corporate profile, service decks, ...)
  guidelines - decks or documents describing the brand rules; their text is passed to the model as rules
A built-in Noventiq 2026 template ships with the app so the page works before anything is uploaded.
"""
import io
import json
import logging
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER as PH
from pptx.util import Emu

ASSETS = Path(__file__).parent / 'noventiq_ppt_assets'
ROOT = Path(os.environ.get('CCC_STATE', '/state')) / 'noventiq-ppt-v1'
BUILTIN_TEMPLATE = 'builtin-template-2026'
KINDS = ('template', 'source', 'guideline')
UPLOAD_LIMIT = 60_000_000
SOURCE_CHARS = 60_000
PPTX = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
LOCK = threading.Lock()
SKIP = {PH.SLIDE_NUMBER, PH.DATE, PH.FOOTER}


# ---------- files ----------
def to_pptx(raw):
    """A .potx has a template content type that python-pptx refuses; rewrite it to a presentation."""
    try:
        src = ZipFile(io.BytesIO(raw))
        names = src.namelist()
    except BadZipFile:
        raise ValueError('That file is not a PowerPoint file.')
    if '[Content_Types].xml' not in names or 'ppt/presentation.xml' not in names:
        raise ValueError('That file is not a PowerPoint file.')
    out = io.BytesIO()
    with ZipFile(out, 'w', 8) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == '[Content_Types].xml':
                data = data.replace(b'presentationml.template.main+xml', b'presentationml.presentation.main+xml')
                data = data.replace(b'ms-powerpoint.template.macroEnabledMain+xml', b'presentationml.presentation.main+xml')
            dst.writestr(info, data)
    return out.getvalue()


def open_deck(raw):
    try:
        return Presentation(io.BytesIO(to_pptx(raw)))
    except ValueError:
        raise
    except Exception:
        raise ValueError('That PowerPoint file could not be opened.')


def _clean(value, limit=400):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def catalog(prs):
    """Layouts of the template: name plus the text placeholders a slide can fill, with their size in inches."""
    layouts = []
    for master in prs.slide_masters:
        for layout in master.slide_layouts:
            fields = []
            for ph in layout.placeholders:
                kind = ph.placeholder_format.type
                if kind in SKIP or kind == PH.PICTURE:
                    continue
                label = _clean(ph.text_frame.text, 30) if ph.has_text_frame else ''
                fields.append({'idx': ph.placeholder_format.idx, 'label': '' if label.lower().startswith('lorem') else label, 'role': 'title' if kind in (PH.TITLE, PH.CENTER_TITLE) else
                               'subtitle' if kind == PH.SUBTITLE else 'text',
                               'w': round(Emu(ph.width or 0).inches, 1), 'h': round(Emu(ph.height or 0).inches, 1)})
            layouts.append({'name': layout.name, 'fields': fields, 'has_picture': any(
                p.placeholder_format.type == PH.PICTURE for p in layout.placeholders)})
    return layouts


def extract_text(raw):
    """Slide-by-slide text of a deck or the brand guideline."""
    prs = open_deck(raw)
    lines = []
    for number, slide in enumerate(prs.slides, 1):
        bits = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = _clean('; '.join(p.text for p in shape.text_frame.paragraphs if p.text.strip()), 700)
                if text:
                    bits.append(text)
            elif getattr(shape, 'has_table', False) and shape.has_table:
                for row in shape.table.rows:
                    cells = [_clean(c.text, 120) for c in row.cells if c.text.strip()]
                    if cells:
                        bits.append(' | '.join(cells))
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            note = _clean(slide.notes_slide.notes_text_frame.text, 400)
            if note:
                bits.append('Notes: ' + note)
        if bits:
            lines.append(f'[Slide {number} - {slide.slide_layout.name}] ' + ' / '.join(bits))
    return '\n'.join(lines)[:SOURCE_CHARS]


# ---------- library ----------
def _store():
    ROOT.mkdir(parents=True, exist_ok=True)
    return ROOT


def _index():
    path = _store() / 'index.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []


def _save_index(items):
    path = _store() / 'index.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(items), encoding='utf-8')
    tmp.replace(path)


BUILTIN_TEXT = {
    'builtin-guideline': ('guideline', 'Noventiq PPT guideline (built in)', 'guideline.txt'),
    'builtin-global-support': ('source', 'Noventiq Global Support Services (built in)', 'global-support-services.txt'),
    'builtin-corporate-profile': ('source', 'Corporate profile 2026 (built in)', 'corporate-profile-2026.txt'),
}


def builtin_items():
    return [{'id': BUILTIN_TEMPLATE, 'kind': 'template', 'name': 'Noventiq PPT template 2026 (built in)', 'builtin': True}] + [
        {'id': key, 'kind': kind, 'name': name, 'builtin': True} for key, (kind, name, _) in BUILTIN_TEXT.items()]


def library():
    items = builtin_items() + _index()
    out = []
    for item in items:
        entry = {k: item[k] for k in ('id', 'kind', 'name') if k in item}
        entry['builtin'] = bool(item.get('builtin'))
        if item['kind'] == 'template':
            try:
                entry['layouts'] = [layout['name'] for layout in catalog(open_deck(template_bytes(item['id'])))]
            except (ValueError, OSError):
                entry['layouts'] = []
        else:
            entry['chars'] = len(source_text(item['id']))
        out.append(entry)
    return out


def template_bytes(item_id):
    if item_id == BUILTIN_TEMPLATE:
        return (ASSETS / 'Noventiq_PPT_template_2026.pptx').read_bytes()
    if not any(i['id'] == item_id and i['kind'] == 'template' for i in _index()):
        raise ValueError('Choose a template from the list.')
    return (_store() / (uuid.UUID(item_id).hex + '.pptx')).read_bytes()


def source_text(item_id):
    if item_id in BUILTIN_TEXT:
        return (ASSETS / BUILTIN_TEXT[item_id][2]).read_text(encoding='utf-8')
    path = _store() / (uuid.UUID(item_id).hex + '.txt') if re.fullmatch(r'[0-9a-f-]{36}', item_id) else None
    try:
        return path.read_text(encoding='utf-8') if path else ''
    except OSError:
        return ''


def add_item(kind, name, raw):
    if kind not in KINDS:
        raise ValueError('Choose Template, Source deck or Guideline.')
    if not raw or len(raw) > UPLOAD_LIMIT:
        raise ValueError('Upload a file up to 60 MB.')
    name = _clean(Path(name or 'deck').name, 120) or 'deck'
    if not re.search(r'\.(pptx|potx)$', name, re.I):
        raise ValueError('Upload a .pptx or .potx file.')
    item_id = str(uuid.uuid4())
    key = uuid.UUID(item_id).hex
    if kind == 'template':
        prs = open_deck(raw)
        if not catalog(prs):
            raise ValueError('That template has no slide layouts.')
        (_store() / (key + '.pptx')).write_bytes(to_pptx(raw))
    else:
        text = extract_text(raw)
        if not text:
            raise ValueError('No text was found in that file.')
        (_store() / (key + '.txt')).write_text(text, encoding='utf-8')
    with LOCK:
        items = _index()
        items.append({'id': item_id, 'kind': kind, 'name': name})
        _save_index(items)
    return item_id


def remove_item(item_id):
    with LOCK:
        items = _index()
        keep = [i for i in items if i['id'] != item_id]
        if len(keep) == len(items):
            raise ValueError('Built-in items cannot be removed.')
        _save_index(keep)
    key = uuid.UUID(item_id).hex
    for suffix in ('.pptx', '.txt'):
        try:
            (_store() / (key + suffix)).unlink()
        except OSError:
            pass


# ---------- planning ----------
INSTRUCTIONS = """You build PowerPoint decks for Noventiq (Seven Seas Technology in the UAE) on the company's own template.
Return one JSON object: {"deck_title": str, "slides": [{"layout": str, "fields": {"<idx>": str | [str, ...]}, "notes": str}]}.
Rules:
- "layout" must be one of the template layout names exactly. "fields" keys must be that layout's idx numbers; fill only idx numbers it lists.
- title fields get a short headline (max ~9 words); subtitle fields a short line; text fields get concise bullets (arrays) or one short paragraph. Size each text to its placeholder (w x h inches): roughly 12 characters per inch of width per line and 3 lines per inch of height at body size. Never overflow.
- Open with a title layout and close with the thank-you / end layout when the template has one. Use section dividers only for decks over 8 slides. Prefer variety across layouts that fit the content (columns for 3 points, numbered lists for steps).
- Facts about Noventiq (numbers, offices, certifications, services, customers) must come from the source decks supplied. Never invent figures, customers or awards. If the brief needs a fact that is not in the sources, write a clear placeholder such as [add customer reference] instead.
- Follow the brand guideline text when supplied (tone, capitalisation, how much text per slide).
- Write in the language of the brief (English by default). "notes" is optional speaker notes (1-2 sentences).
"""


def plan(brief, layouts, sources, guidelines, config, acquire_token, slides_hint):
    from proposal.proposal_studio import proposal_completion
    endpoint = config.get('foundry_endpoint', '').rstrip('/')
    deployment = config.get('foundry_deployment')
    if not config.get('foundry_enabled') or not re.fullmatch(
            r'https://[a-z0-9-]+\.(?:openai\.azure\.com|services\.ai\.azure\.com|cognitiveservices\.azure\.com)', endpoint) or not deployment:
        raise ValueError('Configure the Azure Foundry connection first.')
    parts = [{'type': 'text', 'text': 'TEMPLATE LAYOUTS\n' + json.dumps(layouts, ensure_ascii=False)},
             {'type': 'text', 'text': 'BRIEF\n' + brief}]
    if slides_hint:
        parts.append({'type': 'text', 'text': f'Target about {slides_hint} slides.'})
    for name, text in guidelines:
        parts.append({'type': 'text', 'text': f'BRAND GUIDELINE ({name})\n{text[:20000]}'})
    budget = 60_000
    for name, text in sources:
        take = text[:max(4000, min(len(text), budget))]
        budget = max(0, budget - len(take))
        parts.append({'type': 'text', 'text': f'NOVENTIQ SOURCE DECK ({name})\n{take}'})
    raw = proposal_completion(endpoint, deployment, acquire_token(), INSTRUCTIONS, parts, 9000, structured=True)
    try:
        data = json.loads(raw)
        slides = data['slides']
        if not isinstance(slides, list) or not slides:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise ValueError('The model did not return a usable slide plan. Try again.')
    return data


# ---------- building ----------
def _drop_slides(prs):
    ids = prs.slides._sldIdLst
    for sld in list(ids):
        prs.part.drop_rel(sld.rId)
        ids.remove(sld)


def _fill(shape, value):
    lines = value if isinstance(value, list) else [value]
    lines = [_clean(v, 600) for v in lines if _clean(v, 600)]
    frame = shape.text_frame
    if not lines:
        return False
    frame.text = lines[0]
    for line in lines[1:]:
        frame.add_paragraph().text = line
    return True


def build(template_raw, deck):
    prs = open_deck(template_raw)
    by_name = {}
    for master in prs.slide_masters:
        for layout in master.slide_layouts:
            by_name.setdefault(layout.name, layout)
    _drop_slides(prs)
    used = 0
    for item in deck['slides'][:40]:
        if not isinstance(item, dict):
            continue
        layout = by_name.get(item.get('layout'))
        if layout is None:
            continue
        slide = prs.slides.add_slide(layout)
        fields = item.get('fields') if isinstance(item.get('fields'), dict) else {}
        for ph in list(slide.placeholders):
            idx = ph.placeholder_format.idx
            value = fields.get(str(idx))
            if ph.placeholder_format.type == PH.PICTURE or value is None or not _fill(ph, value):
                ph._element.getparent().remove(ph._element)
        note = _clean(item.get('notes'), 800)
        if note:
            slide.notes_slide.notes_text_frame.text = note
        used += 1
    if not used:
        raise ValueError('The plan did not match any layout in this template.')
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue(), used


# ---------- HTTP ----------
def serve(handler, config, acquire_token):
    owner = handler._user()
    if not owner:
        return handler.send(401, {'error': 'Sign in first.'})
    try:
        size = int(handler.headers.get('Content-Length', '0'))
        if not 0 < size <= UPLOAD_LIMIT + 5_000_000:
            return handler.send(413, {'error': 'Upload a file up to 60 MB.'})
        ctype = handler.headers.get('Content-Type', '')
        body = handler.rfile.read(size)
        if ctype == 'application/octet-stream':
            from urllib.parse import unquote
            item_id = add_item(handler.headers.get('X-Ppt-Kind', ''), unquote(handler.headers.get('X-File-Name', '')), body)
            return handler.send(200, {'id': item_id, 'library': library()})
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError('Invalid request.')
        action = payload.get('action')
        if action == 'list':
            return handler.send(200, {'library': library()})
        if action == 'remove':
            remove_item(str(payload.get('id', '')))
            return handler.send(200, {'library': library()})
        if action != 'generate':
            raise ValueError('Unknown action.')
        if payload.get('consent') is not True:
            raise ValueError('Confirm sending the brief and sources to the configured Foundry model.')
        brief = _clean(payload.get('brief'), 6000)
        if len(brief) < 10:
            raise ValueError('Describe the deck you need.')
        template_id = str(payload.get('template') or BUILTIN_TEMPLATE)
        template_raw = template_bytes(template_id)
        known = {i['id']: i for i in builtin_items() + _index()}
        chosen = [str(x) for x in (payload.get('sources') or [])][:8]
        sources = [(known[i]['name'], source_text(i)) for i in chosen if i in known and known[i]['kind'] == 'source']
        guidelines = [(i['name'], source_text(i['id'])) for i in known.values() if i['kind'] == 'guideline']
        title = _clean(payload.get('title'), 120)
        if title:
            brief = f'Deck title: {title}\n' + brief
        try:
            hint = max(0, min(40, int(payload.get('slides') or 0)))
        except (TypeError, ValueError):
            hint = 0
        layouts = catalog(open_deck(template_raw))
        deck = plan(brief, layouts, sources, guidelines, config, acquire_token, hint)
        data, count = build(template_raw, deck)
        name = re.sub(r'[^A-Za-z0-9 ._-]', '', title or deck.get('deck_title') or 'Noventiq deck').strip() or 'Noventiq deck'
        handler.send_response(200)
        handler.send_header('Content-Type', PPTX)
        handler.send_header('Content-Disposition', f'attachment; filename="{name[:80]} - Noventiq.pptx"')
        handler.send_header('X-Slides', str(count))
        handler.send_header('Content-Length', str(len(data)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.end_headers()
        handler.wfile.write(data)
    except (ValueError, TypeError) as error:
        return handler.send(400, {'error': str(error)})
    except Exception as error:  # network or model failure; keep the page usable
        logging.exception('noventiq_ppt failed')
        return handler.send(502, {'error': 'The deck could not be built: ' + str(error)[:200]})
