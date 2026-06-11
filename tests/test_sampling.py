from chatbot.core.prompt_formatter import ModelFamily
from chatbot.providers.sampling import SamplingSettings, get_sampling_settings
from chatbot.soul.modes import ConversationMode


def test_get_sampling_settings_defaults():
    settings = get_sampling_settings()
    assert isinstance(settings, SamplingSettings)
    assert settings.temperature == 0.7
    assert settings.top_p == 0.95


def test_get_sampling_settings_openai():
    settings = get_sampling_settings(family=ModelFamily.OPENAI, mode=ConversationMode.NORMAL)
    assert settings.temperature == 0.7
    assert settings.presence_penalty == 0.0
    assert settings.frequency_penalty == 0.0


def test_get_sampling_settings_anthropic():
    settings = get_sampling_settings(family=ModelFamily.ANTHROPIC, mode=ConversationMode.NORMAL)
    assert settings.temperature == 0.7
    assert settings.top_p == 0.9


def test_get_sampling_settings_gemini():
    settings = get_sampling_settings(family=ModelFamily.GEMINI, mode=ConversationMode.NORMAL)
    assert settings.temperature == 0.7
    assert settings.top_k == 40


def test_get_sampling_settings_playful_mode():
    settings = get_sampling_settings(family=ModelFamily.OPENAI, mode=ConversationMode.PLAYFUL)
    # base is 0.7, playful adds 0.15 -> 0.85
    assert settings.temperature == 0.85


def test_get_sampling_settings_bedtime_mode():
    settings = get_sampling_settings(family=ModelFamily.GEMINI, mode=ConversationMode.BEDTIME)
    # base is 0.7, bedtime subtracts 0.15 -> 0.55
    assert settings.temperature == 0.55


def test_get_sampling_settings_model_auto_detect():
    # anthropic detection
    settings = get_sampling_settings(model="claude-3-5-sonnet")
    assert settings.top_p == 0.9  # anthropic default
    
    # gemini detection
    settings = get_sampling_settings(model="gemini-1.5-pro")
    assert settings.top_k == 40  # gemini default
    
    # openai detection
    settings = get_sampling_settings(model="gpt-4o")
    assert settings.presence_penalty == 0.0  # openai default
