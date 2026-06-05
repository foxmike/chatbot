import subprocess
import pytest
import os


def test_pi_cli_help_output():
    """Verify pi_cli --help produces expected visual structure"""
    result = subprocess.run(['pi', '--backend', 'pi_cli', '--help'], 
                           capture_output=True, text=True, env={**os.environ, 'NO_COLOR': '1'})
    assert result.returncode == 0
    assert 'Usage: pi [OPTIONS] COMMAND [ARGS]...' in result.stdout
    assert '╭─ Commands ───────────────────────────────────────────╮' in result.stdout
    assert '│ chat    Start interactive chat session             │' in result.stdout


def test_pi_cli_chat_screen():
    """Verify main chat interface renders correctly"""
    result = subprocess.run(['pi', '--backend', 'pi_cli', 'chat', '--test-mode'], 
                           capture_output=True, text=True, timeout=5,
                           env={**os.environ, 'NO_COLOR': '1'})
    assert '╭─ PI Chat Assistant ────────────────────────────────╮' in result.stdout
    assert '│ > Type your message...                           │' in result.stdout
    assert '│                                                  │' in result.stdout