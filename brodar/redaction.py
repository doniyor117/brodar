"""Credential redaction at archive, audit and retrieval boundaries."""

import re
from hamroh.utils.secrets_scrubber import scrub as upstream_scrub

_KNOWN=set()

def register_secrets(*values):
    for value in values:
        if isinstance(value,str) and len(value)>=8:_KNOWN.add(value)

_EXTRA=(re.compile(r'\bAIza[A-Za-z0-9_-]{35}\b'),
        re.compile(r'\bhat_[A-Za-z0-9_-]{16,}\b'),
        # Telegram URLs prefix tokens with "bot", so a leading word boundary
        # would miss the credential inside /botTOKEN and /file/botTOKEN.
        re.compile(r'(?<!\d)\d{6,12}:(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{30,40}(?![A-Za-z0-9_-])'),
        re.compile(r'\bgithub_pat_[A-Za-z0-9_]{40,}\b'))


def scrub(text):
    for value in sorted(_KNOWN,key=len,reverse=True):text=text.replace(value,"[REDACTED]")
    text=upstream_scrub(text)
    for pattern in _EXTRA:text=pattern.sub('[REDACTED]',text)
    return text


def scrub_object(value):
    if isinstance(value,str):return scrub(value)
    if isinstance(value,list):return [scrub_object(x) for x in value]
    if isinstance(value,tuple):return tuple(scrub_object(x) for x in value)
    if isinstance(value,dict):return {k:scrub_object(v) for k,v in value.items()}
    return value
