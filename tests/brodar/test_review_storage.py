from contextlib import contextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from brodar.learning import LearningStore
from brodar.scope import Scope
from brodar.store import ScopedStore


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata,expected', [
    ({'subject_ids': [42], 'confidence': 'tentative'}, ([42], 'tentative')),
    (None, ([73], 'uncertain')),
])
async def test_restore_preserves_recorded_or_current_metadata(metadata, expected):
    store = LearningStore(None, Scope(1, 2))
    store.memory_history = AsyncMock(return_value=[{
        'revision': 2, 'content': 'old note', 'metadata': metadata,
        'current_subject_ids': [73], 'current_confidence': 'uncertain',
    }])
    store.memory_write = AsyncMock(return_value={'revision': 5})
    await store.memory_restore('context/person.md', 2, expected_revision=4, reason='undo')
    kwargs = store.memory_write.call_args.kwargs
    assert kwargs['subject_ids'] == expected[0]
    assert kwargs['confidence'] == expected[1]
    assert kwargs['expected_revision'] == 4


@pytest.mark.asyncio
async def test_semantic_hydration_is_bounded_and_preserves_rank():
    hits = [('message' if i % 2 else 'memory', uuid4()) for i in range(60)]

    class Cursor:
        def __init__(self):
            self.queries = []
        def execute(self, sql, args):
            self.queries.append(sql)
            if 'GROUP BY kind' in sql:
                self.description = [('kind',), ('source_id',)]
                self.rows = hits
            elif 'id=ANY' in sql:
                kind = 'message' if 'brodar_v2.messages' in sql else 'memory'
                self.description = [('kind',), ('id',), ('content',)]
                # Database order deliberately differs from similarity order.
                self.rows = [(kind, uid, str(uid)) for k, uid in reversed(hits) if k == kind]
            else:
                self.description = [('kind',), ('id',), ('content',)]
                self.rows = []
        def fetchall(self):
            return self.rows

    cursor = Cursor()
    class DB:
        @contextmanager
        def transaction(self, *args, **kwargs):
            yield cursor
        async def run(self, operation):
            return operation()

    result = await ScopedStore(DB(), Scope(1, 2)).search('topic', vector=[0.1]*768, limit=30)
    assert len(cursor.queries) == 5  # two lexical + ranked IDs + two bulk hydration
    assert [(row['kind'], row['id']) for row in result] == hits[:30]
