from types import SimpleNamespace
import pytest
import openai
from playlist_maker.planner import ai_plan, PlanningError


def stub(monkeypatch, content):
    captured = {}
    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=self)
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
    return captured


def test_ai_outputs_deduplicated_queries_and_only_user_preferences(monkeypatch):
    captured = stub(monkeypatch, '{"queries": ["Miles Davis So What", "Miles Davis So What", "Nina Simone Feeling Good"]}')
    assert len(ai_plan('джаз / jazz / jazz français', 30, 'test-only', 'test-model')) == 2
    user = captured['messages'][1]['content']
    assert '30 minutes' in user and 'джаз' in user
    assert captured['response_format'] == {'type': 'json_object'}


@pytest.mark.parametrize('content', ['not json', '{}', '{"queries": []}',
                                    '{"queries": [null]}', '{"queries": [123]}', '[]'])
def test_bad_model_response_is_explained(monkeypatch, content):
    stub(monkeypatch, content)
    with pytest.raises(PlanningError, match='демонстрационный'):
        ai_plan('music', 30, 'test-only', 'model')
