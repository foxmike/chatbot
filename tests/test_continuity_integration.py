import pytest
from backends.pi_cli import PiCliBackend
from runtime.agency import Agency


def test_continuity_state_persists_across_steps():
    """Verify continuity state is maintained between agency execution steps"""
    backend = PiCliBackend(model="openrouter/qwen/qwen3-235b-a22b-thinking-2507")
    agency = Agency(backend=backend, selected_backend="pi_cli")

    # Initial execution with state
    result1 = agency.execute(
        task_description="Analyze code",
        context="def hello(): return 'world'",
        state={"analysis_stage": "parsing"}
    )

    # Subsequent execution should maintain continuity
    result2 = agency.execute(
        task_description="Generate test",
        context=result1["output"],
        state=result1.get("state")
    )

    assert result2["state"]["analysis_stage"] == "testing"
    assert "parsing" in result2["output"]
    assert backend.model == "openrouter/qwen/qwen3-235b-a22b-thinking-2507"


def test_backend_initialization_with_specified_model():
    """Verify selected backend initializes with correct model configuration"""
    backend = PiCliBackend(
        model="openrouter/qwen/qwen3-235b-a22b-thinking-2507",
        verification_profile="integration"
    )
    assert backend.config["model"] == "openrouter/qwen/qwen3-235b-a22b-thinking-2507"
    assert backend.config["provider"] == "openrouter"
    assert "qwen3-235b" in backend.config["model_name"]


def test_continuity_failure_recovery():
    """Verify state rollback on execution failure maintains continuity constraints"""
    agency = Agency(
        backend=PiCliBackend(model="openrouter/qwen/qwen3-235b-a22b-thinking-2507"),
        max_retries=1
    )
    initial_state = {"phase": "E", "task_id": "e.11"}

    with pytest.raises(RuntimeError):
        agency.execute(
            task_description="Invalid operation",
            context="",
            state=initial_state.copy()
        )

    # Verify no state mutation occurred
    assert agency.state == initial_state