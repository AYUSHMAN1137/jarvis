"""Block until the J.A.R.V.I.S HTTP server answers /health."""

import sys
import time
import urllib.request

BASE = "http://localhost:8000"
attempts = int(sys.argv[1]) if len(sys.argv) > 1 else 90

for attempt in range(attempts):
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=3) as resp:
            if resp.status == 200:
                print(f"server ready after {attempt * 2}s")
                sys.exit(0)
    except Exception:
        time.sleep(2)
print("server did not come up")
sys.exit(1)
