import subprocess
import time
import os
import pytest

def test_timeline_survives_restart(tmp_path):
    """Verify timeline state persists across application restarts"""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    env = {**os.environ, "PI_STATE_DIR": str(state_dir)}

    # Start application and create timeline event
    start_proc = subprocess.Popen(["pi", "start"], env=env)
    time.sleep(1.5)  # Allow initialization
    subprocess.run(["pi", "timeline", "add", "verification_event"], 
                  env=env, check=True, capture_output=True)

    # Stop application
    subprocess.run(["pi", "stop"], env=env, check=True)
    time.sleep(0.5)

    # Restart application
    start_proc = subprocess.Popen(["pi", "start"], env=env)
    time.sleep(1.5)

    # Verify timeline event persists
    result = subprocess.run(["pi", "timeline", "list"], 
                          env=env, capture_output=True, text=True, check=True)
    assert "verification_event" in result.stdout

    # Cleanup
    subprocess.run(["pi", "stop"], env=env)
    start_proc.terminate()