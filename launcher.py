"""Portable Windows/local launcher."""

from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.request
import webbrowser


def _open_browser(url: str) -> None:
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{url}/api/health", timeout=1)
            webbrowser.open(url)
            return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)


def main() -> None:
    import uvicorn

    host = "127.0.0.1"
    port = int(os.getenv("CKO_IBS_PORT", "8766"))
    url = f"http://{host}:{port}"
    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    uvicorn.run("cko_ibs.main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
