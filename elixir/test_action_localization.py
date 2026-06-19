"""Action node prompt tests."""
from nodes.action import ACTION_SYSTEM_PROMPT


def test_action_prompt_includes_localization_rules():
    assert "Localization rules:" in ACTION_SYSTEM_PROMPT
    assert 'locale is NOT "en"' in ACTION_SYSTEM_PROMPT
