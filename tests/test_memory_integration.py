import pytest
from backends.pi_cli import PiCliBackend
from memory.core import MemoryCore

@pytest.fixture
def memory_system():
    """Fixture providing memory system wired through CLI backend."""
    backend = PiCliBackend()
    memory = MemoryCore(backend)
    yield memory
    # Clear test memory after each run
    memory.clear_all()

def test_basic_memory_storage_retrieval(memory_system):
    """Test storing and retrieving a simple memory entry."""
    memory_system.store("user_preferences", {"theme": "dark", "language": "en"})
    prefs = memory_system.retrieve("user_preferences")
    assert prefs is not None
    assert prefs["theme"] == "dark"
    assert prefs["language"] == "en"

def test_memory_persistence_across_sessions(memory_system):
    """Test memory survives CLI backend restarts (simulated)."""
    memory_system.store("session_count", 1)
    
    # Simulate backend reset
    new_memory = MemoryCore(PiCliBackend())
    count = new_memory.retrieve("session_count")
    
    assert count == 1

def test_memory_isolation_between_users(memory_system):
    """Test memory entries don't leak between different user contexts."""
    memory_system.set_context("user_123")
    memory_system.store("chat_history", ["Hello"])
    
    memory_system.set_context("user_456")
    memory_system.store("chat_history", ["Hi"])
    
    memory_system.set_context("user_123")
    assert memory_system.retrieve("chat_history") == ["Hello"]