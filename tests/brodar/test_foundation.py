from pathlib import Path

import pytest
import httpx

from brodar.brains.base import terminal
from brodar.brains.codex import child_environment
from brodar.embeddings import EmbeddingError, GeminiEmbeddings
from brodar.scope import Scope
from brodar.store import context_path


@pytest.mark.parametrize('path',['SYSTEM.md','prompts/brodar.md','context/../SYSTEM.md',
                                '/context/a.md','context//a.md','context/a\\b.md','context/a\nb.md'])
def test_context_cannot_address_main_prompt(path):
    with pytest.raises(ValueError):
        context_path(path)


def test_audiences_share_topics_but_sessions_do_not():
    a,b = Scope(1,-100,10),Scope(1,-100,20)
    assert a.audience == b.audience
    assert a.conversation != b.conversation
    assert a.audience != Scope(1,100).audience
    assert a.audience == Scope(2,-100,10).audience  # bot replacement retains deployment history


def test_terminal_text_cannot_become_reply_fallback():
    result = terminal('internal reasoning or accidentally pasted credentials','session')
    assert result.status == 'failed'
    assert result.error == 'invalid_terminal_output'
    assert result.action is None
    assert terminal('{"action":"skip","reason":"just group chatter"}','s').status=='completed'
    assert terminal('{"action":"stop"}','s').status=='failed'
    assert terminal('{"action":"stop","reason":"ok","reply":"hello"}','s').status=='failed'


def test_provider_environment_does_not_inherit_service_secrets(monkeypatch,tmp_path):
    for key in ['NEON_API_KEY','GOOGLE_AI_API_KEY','TELEGRAM_BOT_TOKEN','GITHUB_TOKEN','DATABASE_URL']:
        monkeypatch.setenv(key,'secret')
    env = child_environment(tmp_path)
    assert not any(k in env for k in ['NEON_API_KEY','GOOGLE_AI_API_KEY','TELEGRAM_BOT_TOKEN','GITHUB_TOKEN','DATABASE_URL'])
    assert env['CODEX_HOME']==str(tmp_path)
    assert env['HOME']==str(tmp_path/'user')


@pytest.mark.asyncio
async def test_embedding_2_uses_prompt_formats_and_header_key():
    requests=[]
    def handle(request):
        import json
        body=json.loads(request.content)
        requests.append(body)
        assert request.headers['x-goog-api-key']=='private-key'
        assert 'private-key' not in str(request.url)
        assert 'taskType' not in body and 'task_type' not in body
        assert body['outputDimensionality']==768
        return httpx.Response(200,json={'embedding':{'values':[1.0]+[0.0]*767}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        embed=GeminiEmbeddings('private-key',client=client)
        assert len(await embed.embed('salom'))==768
        assert len(await embed.embed('привет',query=True))==768
    assert requests[0]['content']['parts'][0]['text']=='title: conversation context | text: salom'
    assert requests[1]['content']['parts'][0]['text']=='task: search result | query: привет'


@pytest.mark.asyncio
async def test_embedding_errors_do_not_echo_vendor_payload_or_key():
    def handle(request):
        return httpx.Response(429,json={'error':'private-key / private conversation'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(EmbeddingError,match='HTTP 429') as exc:
            await GeminiEmbeddings('private-key',client=client).embed('private conversation')
        assert 'private' not in str(exc.value)
