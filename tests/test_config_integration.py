import pytest
import tempfile
import json
import os
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


def test_config_file_loading_integration():
    """Test that config can load from file and affects system behavior"""
    config_data = {
        "system_prompt": "You are a test assistant for integration testing.",
        "persona": "tester",
        "persona_details": {
            "phase": "integration_testing",
            "focus": "end-to-end_verification"
        },
        "model": {
            "name": "test-model",
            "temperature": 0.1,
            "max_tokens": 1000
        },
        "ui": {
            "theme": "test_theme",
            "compact_mode": True
        }
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config_data, f)
        config_path = f.name
    
    try:
        # Load config from file
        config = Config.load_from_file(config_path)
        
        # Verify values loaded correctly
        assert config.system_prompt == "You are a test assistant for integration testing."
        assert config.persona == "tester"
        assert config.model.temperature == 0.1
        assert config.ui.compact_mode is True
        
        # Verify changes propagate to prompt generation
        prompt = generate_prompt(config)
        assert "integration testing" in prompt
        assert "end-to-end_verification" in prompt
        
    finally:
        os.unlink(config_path)


def test_config_save_load_roundtrip():
    """Test saving and loading config preserves all settings"""
    original = Config()
    original.system_prompt = "Roundtrip test prompt"
    original.persona = "qa_engineer"
    original.model.temperature = 0.8
    original.ui.theme = "roundtrip_test"
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        save_path = f.name
    
    try:
        # Save config
        original.save_to_file(save_path)
        
        # Load config
        loaded = Config.load_from_file(save_path)
        
        # Verify all values persisted correctly
        assert loaded.system_prompt == original.system_prompt
        assert loaded.persona == original.persona
        assert loaded.model.temperature == original.model.temperature
        assert loaded.ui.theme == original.ui.theme
        
        # Verify prompts are identical
        original_prompt = generate_prompt(original)
        loaded_prompt = generate_prompt(loaded)
        assert original_prompt == loaded_prompt
        
    finally:
        os.unlink(save_path)


def test_config_merge_from_partial():
    """Test merging partial config updates without losing existing settings"""
    config = Config()
    config.system_prompt = "Original system prompt"
    config.persona = "original_persona"
    config.model.temperature = 0.5
    
    # Create partial update
    partial_update = {
        "system_prompt": "Updated system prompt",
        "model": {
            "temperature": 0.9  # Only update temperature
        }
    }
    
    # Apply partial update
    config.merge_from_dict(partial_update)
    
    # Verify specific updates applied
    assert config.system_prompt == "Updated system prompt"
    assert config.model.temperature == 0.9
    
    # Verify unchanged values preserved
    assert config.persona == "original_persona"


def test_config_validation_integration():
    """Test config validation catches integration-level errors"""
    config = Config()
    
    # Test invalid persona configuration
    with pytest.raises(ValueError):
        config.persona = "invalid_nonexistent_persona"
        config.validate()
    
    # Test invalid logic in system prompt templates
    with pytest.raises(ValueError):
        config.system_prompt_template = "Missing required {{persona}} variable"
        config.validate()
    
    # Reset for next test
    config.persona = "default"
    
    # Test invalid model configuration
    with pytest.raises(ValueError):
        config.model.temperature = 2.5  # Invalid range
        config.validate()
    
    # Test invalid UI configuration
    with pytest.raises(ValueError):
        config.ui.theme = "nonexistent_theme"
        config.validate()


def test_config_real_world_scenario():
    """Test real-world config usage scenario across all components"""
    # Create production-like config
    config = Config()
    config.system_prompt = "You are a senior software architect"
    config.persona = "senior_developer"
    config.persona_details = {
        "years_experience": 15,
        "focus": "system_design",
        "communication_style": "precise_technical"
    }
    config.model.name = "claude-3-5-sonnet-20241022"
    config.model.temperature = 0.2
    config.model.max_tokens = 2000
    config.ui.theme = "dark" 
    config.ui.compact_mode = False
    
    # Verify all components integrate correctly
    prompt = generate_prompt(config)
    
    # Check prompt includes all persona details
    assert "senior developer" in prompt.lower()
    assert "system_design" in prompt.lower()
    assert "technical" in prompt.lower()
    
    # Check model config is accessible
    assert config.model.name == "claude-3-5-sonnet-20241022"
    assert 0 <= config.model.temperature <= 1
    
    # Check UI config is properly scoped
    assert config.ui.theme in ["dark", "light", "auto"]
    assert isinstance(config.ui.compact_mode, bool)


def test_config_error_handling_integration():
    """Test error handling during config loading and integration"""
    
    # Test corrupt JSON file handling
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write('{"invalid": json content,}')
        bad_json_path = f.name
    
    try:
        with pytest.raises(json.JSONDecodeError):
            Config.load_from_file(bad_json_path)
    finally:
        os.unlink(bad_json_path)
    
    # Test missing required file sections
    incomplete_config = {"system_prompt": "test", "ui": {"theme": "dark"}}
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(incomplete_config, f)
        incomplete_path = f.name
    
    try:
        # Should load with defaults for missing sections
        config = Config.load_from_file(incomplete_path)
        assert config.system_prompt == "test"
        assert config.ui.theme == "dark"
        # Should have defaults for missing model settings
        assert hasattr(config, 'model')
        assert hasattr(config.model, 'temperature')
        
    finally:
        os.unlink(incomplete_path)


def test_config_thread_safety():
    """Test config doesn't have race conditions in multi-threaded usage"""
    import threading
    import time
    
    config = Config()
    results = []
    
    def worker(worker_id):
        """Worker to modify config safely"""
        local_config = config.copy()
        local_config.system_prompt = f"Worker {worker_id} prompt"
        local_config.persona = f"worker_{worker_id}"
        prompt = generate_prompt(local_config)
        results.append((worker_id, prompt))
    
    # Run multiple workers concurrently
    threads = []
    for i in range(5):
        thread = threading.Thread(target=worker, args=(i,))
        threads.append(thread)
        thread.start()
    
    # Wait for all workers
    for thread in threads:
        thread.join()
    
    # Verify all workers got unique results
    worker_ids = [result[0] for result in results]
    unique_workers = set(worker_ids)
    assert len(unique_workers) == 5
    assert len(results) == 5
    
    # Verify original config unchanged
    assert config.system_prompt != "Worker 4 prompt"
    
    # Verify each worker got unique prompt
    prompts = [result[1] for result in results]
    assert len(set(prompts)) == 5