#!/usr/bin/env python3
"""Helper script for test_validator_processes.py.

Spawns a grandchild that sleeps for a long time, then sleeps itself.
If the caller sends SIGTERM to the process group of this script,
the grandchild should also die — lope's safe subprocess runner
relies on `os.setsid` to put everything in one killable group.

Usage:
    python3 spawn_tree.py [sleep_seconds]
    # parent sleeps <sleep_seconds>, grandchild sleeps <sleep_seconds>*10
"""
import os
import subprocess
import sys
import time

sleep_sec = int(sys.argv[1]) if len(sys.argv) > 1 else 60

# Spawn a grandchild that sleeps much longer than the parent.
# If the process-group kill works, this grandchild will be killed too.
grandchild = subprocess.Popen(
    [sys.executable, "-c", f"import time; time.sleep({sleep_sec * 10})"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

print(f"PID={os.getpid()} GC_PID={grandchild.pid}", flush=True)
time.sleep(sleep_sec)
