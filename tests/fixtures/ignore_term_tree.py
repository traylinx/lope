from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time


def ignore(_signum, _frame):
    return None


signal.signal(signal.SIGTERM, ignore)
child = subprocess.Popen([
    sys.executable,
    "-c",
    "import signal,time; signal.signal(signal.SIGTERM, lambda *_: None); time.sleep(120)",
])
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({"child": os.getpid(), "grandchild": child.pid}, handle)
    handle.flush()
    os.fsync(handle.fileno())
while True:
    time.sleep(1)
