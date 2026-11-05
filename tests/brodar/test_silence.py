import pytest

from brodar.brains.base import terminal


@pytest.mark.parametrize('text',['[SILENT]','[silent]','  [SILENT]\n'])
def test_standalone_silence_is_an_internal_skip(text):
    outcome=terminal(text,'thread')
    assert outcome.status=='completed' and outcome.action=='skip'
    assert not outcome.internal_text


def test_silence_token_does_not_strip_arbitrary_text():
    assert terminal('hello\n[SILENT]','thread').status=='failed'
