import subprocess

def test_smoke():
    """Verify end-to-end chatbot execution with pi_cli backend"""
    result = subprocess.run([
        'python', '-m', 'chatbot.cli',
        '--backend', 'pi_cli',
        '--model', 'openrouter/qwen/qwen3-235b-a22b-thinking-2507',
        'Hello, smoke test'
    ], capture_output=True, text=True)
    
    assert result.returncode == 0, f"Process failed: {result.stderr}"
    assert "smoke test" in result.stdout.lower(), "Unexpected response content"