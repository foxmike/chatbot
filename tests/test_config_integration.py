import pytest
from chatbot.config import Config
from chatbot.prompts import generate_prompt

def test_config_affects_system_prompt():
    """Verify config changes propagate to generated prompts"""
    original_config = Config()
    original_prompt = generate_prompt(original_config)

    # Create modified config
    test_config = Config()
    test_config.system_prompt = "You are a security-focused AI assistant."

    modified_prompt = generate_prompt(test_config)
    assert "security-focused" in modified_prompt
    assert original_prompt != modified_prompt


def test_config_affects_persona_prompt():
    """Verify persona configuration changes affect prompts"""
    config = Config()
    config.persona = "developer"
    config.persona_details = {"expertise": "security", "style": "concise"}

    prompt = generate_prompt(config)
    assert "security" in prompt
    assert "concise" in prompt