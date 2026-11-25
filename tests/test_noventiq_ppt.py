import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proposal import noventiq_ppt as n  # noqa: E402


class Handler:
    def __init__(self, body, ctype='application/json', headers=None):
        self.rfile = io.BytesIO(body)
        self.headers = {'Content-Length': str(len(body)), 'Content-Type': ctype, **(headers or {})}
        self.out = None
        self.wfile = io.BytesIO()
        self.sent = {}

    def _user(self):
        return 'a@b.c'

    def send(self, code, data):
        self.out = (code, data)

    def send_response(self, code):
        self.out = (code, None)

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


class NoventiqPptTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(n, 'ROOT', Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.raw = n.template_bytes(n.BUILTIN_TEMPLATE)

    def test_catalog_lists_layouts_with_labels(self):
        layouts = {l['name']: l for l in n.catalog(n.open_deck(self.raw))}
        self.assertIn('Three Columns', layouts)
        self.assertIn('Thank You', layouts)
        self.assertTrue(any(f['role'] == 'title' for f in layouts['Headline + Text']['fields']))

    def test_build_fills_layouts_and_drops_unknown(self):
        deck = {'slides': [
            {'layout': 'Title – Visual', 'fields': {'0': 'Azure Migration', '1': 'For Acme'}, 'notes': 'n'},
            {'layout': 'No such layout', 'fields': {'0': 'x'}},
            {'layout': 'Headline + Text', 'fields': {'0': 'Why', '2': ['a', 'b']}},
        ]}
        data, count = n.build(self.raw, deck)
        self.assertEqual(count, 2)
        text = n.extract_text(data)
        self.assertIn('Azure Migration', text)
        self.assertIn('a; b', text)
        self.assertNotIn('Lorem', text)

    def test_build_rejects_plan_without_matching_layout(self):
        with self.assertRaises(ValueError):
            n.build(self.raw, {'slides': [{'layout': 'nope', 'fields': {}}]})

    def test_library_upload_and_remove(self):
        data, _ = n.build(self.raw, {'slides': [{'layout': 'Headline + Text', 'fields': {'0': 'Hello', '2': 'World'}}]})
        item = n.add_item('source', 'My deck.pptx', data)
        names = {i['id']: i for i in n.library()}
        self.assertEqual(names[item]['kind'], 'source')
        self.assertIn('Hello', n.source_text(item))
        tpl = n.add_item('template', 'T.potx', self.raw)
        self.assertGreater(len(next(i for i in n.library() if i['id'] == tpl)['layouts']), 5)
        n.remove_item(item)
        self.assertNotIn(item, [i['id'] for i in n.library()])
        with self.assertRaises(ValueError):
            n.remove_item(n.BUILTIN_TEMPLATE)
        with self.assertRaises(ValueError):
            n.add_item('source', 'x.pdf', b'abc')
        with self.assertRaises(ValueError):
            n.add_item('source', 'x.pptx', b'not a zip')

    def test_builtin_sources_have_text(self):
        for key in n.BUILTIN_TEXT:
            self.assertGreater(len(n.source_text(key)), 1000)

    def test_serve_generate_end_to_end_with_fake_model(self):
        deck = {'deck_title': 'T', 'slides': [{'layout': 'Headline + Text', 'fields': {'0': 'Hi', '2': ['x']}}]}
        seen = {}

        def fake(endpoint, deployment, token, instructions, parts, tokens, structured=False):
            seen['text'] = ' '.join(p['text'] for p in parts)
            return json.dumps(deck)
        cfg = lambda: None  # noqa: E731
        config = {'foundry_enabled': True, 'foundry_endpoint': 'https://x.openai.azure.com', 'foundry_deployment': 'd'}
        body = json.dumps({'action': 'generate', 'brief': 'A deck about Azure for Acme', 'consent': True,
                           'sources': ['builtin-corporate-profile']}).encode()
        h = Handler(body)
        with mock.patch('proposal.proposal_studio.proposal_completion', fake):
            n.serve(h, config, lambda: 'tok')
        self.assertEqual(h.out[0], 200)
        self.assertIn('NOVENTIQ SOURCE DECK', seen['text'])
        self.assertIn('BRAND GUIDELINE', seen['text'])
        self.assertIn('Three Columns', seen['text'])
        self.assertIn('Hi', n.extract_text(h.wfile.getvalue()))

    def test_generate_requires_consent_and_brief(self):
        for payload in ({'action': 'generate', 'brief': 'long enough brief here'},
                        {'action': 'generate', 'brief': 'x', 'consent': True}):
            h = Handler(json.dumps(payload).encode())
            n.serve(h, {}, lambda: 't')
            self.assertEqual(h.out[0], 400)


if __name__ == '__main__':
    unittest.main()
