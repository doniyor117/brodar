"""Credential redaction at archive, audit and retrieval boundaries."""

import re
from hamroh.utils.secrets_scrubber import scrub as upstream_scrub

_EXTRA=(re.compile(r'\bAIza[A-Za-z0-9_-]{35}\b'),
        re.compile(r'\bgithub_pat_[A-Za-z0-9_]{40,}\b'))


def scrub(text):
    text=upstream_scrub(text)
    for pattern in _EXTRA:text=pattern.sub('[REDACTED]',text)
    return text


def scrub_object(value):
    if isinstance(value,str):return scrub(value)
    if isinstance(value,list):return [scrub_object(x) for x in value]
    if isinstance(value,tuple):return tuple(scrub_object(x) for x in value)
    if isinstance(value,dict):return {k:scrub_object(v) for k,v in value.items()}
    return value
