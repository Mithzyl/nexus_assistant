"""Request-scoped public answer projection, never reasoning or draft JSON."""
import json
import re
from contextvars import ContextVar

sink = ContextVar('intake_stream_sink', default=None)

def answer_prefix(raw):
    if re.search(r'"final_text"\s*:\s*"', raw):
        raw = string_prefix(raw, "final_text")
    return question_prefix(raw)

def question_prefix(raw):
    # Only the explicit public question answer field can be displayed before validation.
    if not re.search(r'"intent"\s*:\s*"question"', raw):
        return ''
    return string_prefix(raw, "answer")

def string_prefix(raw, key):
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*"', raw)
    if not match:
        return ''
    fragment = raw[match.end()-1:]
    try:
        value, _ = json.JSONDecoder().raw_decode(fragment)
        return value if isinstance(value,str) else ''
    except ValueError:
        # Trim only a potentially incomplete JSON escape at a chunk boundary.
        for n in range(min(7,len(fragment))):
            try:
                return json.loads((fragment[:-n] if n else fragment) + '"')
            except ValueError:
                pass
    return ''
