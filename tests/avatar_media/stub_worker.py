"""Stand-in child process: speaks the worker JSON-lines protocol, with selectable misbehaviour."""
import json
import os
import signal
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "good"


def emit(**m):
    print(json.dumps(m), flush=True)


if mode == "crash_before_ready":
    sys.exit(3)
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("noise that is not json", flush=True)
emit(type="ready", pid=os.getpid())
for line in sys.stdin:
    command = json.loads(line)
    if command["type"] == "stop":
        if mode in ("ignore_stop", "ignore_term"):
            continue
        break
    if command["type"] == "crash":
        os._exit(9)
    emit(type="echo", **{k: v for k, v in command.items() if k != "type"})
    if mode == "frames" and command["type"] == "burst":
        for i in range(command["n"]):
            emit(type="frame", kind="speech" if i == 0 else "idle", first=i == 0, n=i)
if mode == "ignore_term":
    time.sleep(60)
