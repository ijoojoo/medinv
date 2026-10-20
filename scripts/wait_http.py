"""Wait for a local development HTTP service without logging response bodies."""
import sys
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

url=sys.argv[1]
if urlsplit(url).hostname not in {"127.0.0.1","localhost"}:
    raise SystemExit("This development helper only accepts loopback URLs")
for _ in range(90):
    try:
        with urlopen(url,timeout=2) as response:
            if response.status == 200:
                print("Local HTTP service ready")
                break
    except (URLError,OSError):
        pass
    time.sleep(2)
else:
    raise SystemExit("Service readiness timeout")
