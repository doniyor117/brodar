"""Trusted audience identities, constructed by the Telegram dispatcher only."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    bot_id: int
    chat_id: int
    topic_id: int = 0
    instance_id: str = 'brodar'

    @property
    def audience(self) -> str:
        # Topics share group knowledge; provider sessions remain separate.
        return f"telegram:{self.instance_id}:{self.chat_id}"

    @property
    def conversation(self) -> str:
        return f"{self.audience}:{self.topic_id}"
