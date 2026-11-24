import unittest

import attachments

PNG = 'data:image/png;base64,iVBORw0KGgo='


class AttachmentsTest(unittest.TestCase):
    def test_clean_keeps_only_small_images(self):
        got = attachments.clean([PNG, 'data:text/html;base64,AAAA', 'http://x/y.png', 5, PNG * 1 + 'A' * attachments.MAX_BYTES])
        self.assertEqual(got, [PNG])

    def test_limit_four(self):
        self.assertEqual(len(attachments.clean([PNG] * 9)), 4)

    def test_content_parts(self):
        parts = attachments.content('hello', [PNG])
        self.assertEqual(parts[0], {'type': 'text', 'text': 'hello'})
        self.assertEqual(parts[1]['image_url']['url'], PNG)
        self.assertEqual(attachments.content('hello', []), 'hello')

    def test_ask_sends_picture_then_falls_back(self):
        calls = []

        def chat(messages, tokens):
            calls.append(messages[-1]['content'])
            if isinstance(messages[-1]['content'], list):
                raise ValueError('vision not supported')
            return 'ok', 'm'

        answer, _ = attachments.ask(chat, 'sys', 'task', 100, [PNG])
        self.assertEqual(answer, 'ok')
        self.assertIsInstance(calls[0], list)
        self.assertIn('could not read them', calls[1])


if __name__ == '__main__':
    unittest.main()
