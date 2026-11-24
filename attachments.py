"""Pictures the operator attaches in chat (screenshots, error dialogs, diagrams) for the vision-capable model."""
import re

MAX_IMAGES = 4
MAX_BYTES = 1_600_000          # per image, base64 text length; the page shrinks pictures before sending
DATA_URL = re.compile(r'data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+')


def clean(images):
    """Keep only small png/jpeg/webp data URLs (at most four)."""
    out = []
    for item in (images if isinstance(images, list) else [])[:MAX_IMAGES]:
        if isinstance(item, str) and len(item) <= MAX_BYTES and DATA_URL.fullmatch(item):
            out.append(item)
    return out


def content(text, images):
    if not images:
        return text
    return [{'type': 'text', 'text': text}] + [{'type': 'image_url', 'image_url': {'url': url}} for url in images]


NOTE = ('\nIMPORTANT: the operator attached picture(s). Read them carefully (text, error messages, names, numbers) and '
        'put the facts you read into "thought", because you will not see the picture again.')


def ask(chat, system, message, max_tokens, images=None):
    """chat(messages, max_tokens) -> (answer, model). Falls back to text only if the model cannot take pictures."""
    images = clean(images)
    if images:
        try:
            return chat([{'role': 'system', 'content': system}, {'role': 'user', 'content': content(message + NOTE, images)}], max_tokens)
        except ValueError:
            message += '\n(The operator attached picture(s) but this model could not read them: say so briefly and work from the text.)'
    return chat([{'role': 'system', 'content': system}, {'role': 'user', 'content': message}], max_tokens)
