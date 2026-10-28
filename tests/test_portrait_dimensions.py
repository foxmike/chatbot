"""Portrait request defaults follow AVTR's source frame, not its face crop."""
from chatbot.api.visual import GenerateRequest
from chatbot.visual.generation import OpenRouterImageGenerationProvider


def test_portrait_request_defaults_and_provider_aspect_ratio():
    request = GenerateRequest(prompt="Centered head-and-shoulders portrait")
    assert (request.width, request.height) == (1280, 720)
    provider = OpenRouterImageGenerationProvider("openrouter/test/model")
    payload = provider._build_payload(
        model="test/model", prompt=request.prompt, negative_prompt=None,
        width=request.width, height=request.height, seed=None,
    )
    assert payload["image_config"]["aspect_ratio"] == "16:9"


def test_explicit_nonportrait_dimensions_remain_supported():
    request = GenerateRequest(prompt="Square image", width=512, height=512)
    assert (request.width, request.height) == (512, 512)
