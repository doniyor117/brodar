from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class Outcome:
    status: Literal['completed','failed','interrupted']
    action: Literal['stop','skip','heartbeat'] | None = None
    reason: str = ''
    error: str | None = None
    session_id: str | None = None
    # Internal output is deliberately never a public reply fallback.
    internal_text: str = field(default='',repr=False)


class Brain(Protocol):
    session_id: str | None

    async def start(self, session_id: str | None = None) -> None: ...
    async def turn(self, text: str) -> Outcome: ...
    async def steer(self, text: str) -> bool: ...
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...


def terminal(text: str, session_id: str | None) -> Outcome:
    import json
    try:
        value = json.loads(text)
        if not isinstance(value,dict) or set(value)-{'action','reason'}:
            raise ValueError('Unexpected terminal fields')
        action = value['action']
        reason = value.get('reason','')
        if action not in {'stop','skip','heartbeat'} or not isinstance(reason,str):
            raise ValueError('Invalid terminal action')
        if action != 'heartbeat' and not reason.strip():
            raise ValueError('Missing terminal reason')
        if len(reason)>100:
            raise ValueError('Terminal reason too long')
        return Outcome('completed',action,reason,session_id=session_id)
    except (KeyError,ValueError,TypeError):
        return Outcome('failed',error='invalid_terminal_output',session_id=session_id,internal_text=text)
