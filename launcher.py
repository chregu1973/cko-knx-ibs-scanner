"""Portable Windows/local launcher."""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


def _open_browser(url: str) -> None:
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{url}/api/health", timeout=1)
            webbrowser.open(url)
            return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)


def main() -> None:
    try:
        import uvicorn

        # Import the application object directly. Besides giving clearer runtime
        # errors, this ensures PyInstaller includes the complete local package.
        from cko_ibs.main import app, set_shutdown_handler

        host = "127.0.0.1"
        port = int(os.getenv("CKO_IBS_PORT", "8766"))
        url = f"http://{host}:{port}"
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        set_shutdown_handler(lambda: setattr(server, "should_exit", True))
        server.run()
    except Exception:  # noqa: BLE001 - top-level crash reporter must catch startup failures
        error_text = traceback.format_exc()
        base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
        log_path = base_dir / "CKO-KNX-IBS-error.log"
        try:
            log_path.write_text(error_text, encoding="utf-8")
        except OSError:
            log_path = Path(os.getenv("TEMP", ".")) / "CKO-KNX-IBS-error.log"
            log_path.write_text(error_text, encoding="utf-8")
        print("\nCKO KNX IBS Scanner konnte nicht gestartet werden.\n")
        print(error_text)
        print(f"Die Fehlermeldung wurde gespeichert unter:\n{log_path}\n")
        input("Zum Schließen die Eingabetaste drücken ...")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
