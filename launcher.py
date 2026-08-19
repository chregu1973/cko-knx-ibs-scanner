"""Portable Windows/local launcher."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


def _runtime_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()


def _show_startup_error(message: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "CKO KNX IBS Scanner", 0x10)
    except (AttributeError, OSError):
        pass


def _open_browser(url: str) -> None:
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{url}/api/health", timeout=1)
            webbrowser.open(url)
            return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)


def main() -> None:
    log_path = _runtime_dir() / "CKO-KNX-IBS.log"
    try:
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            encoding="utf-8",
        )
    except OSError:
        log_path = Path(os.getenv("TEMP", ".")) / "CKO-KNX-IBS.log"
        logging.basicConfig(filename=log_path, level=logging.INFO, encoding="utf-8")
    try:
        import uvicorn

        # Import the application object directly. Besides giving clearer runtime
        # errors, this ensures PyInstaller includes the complete local package.
        from cko_ibs.main import app, set_shutdown_handler

        host = "127.0.0.1"
        port = int(os.getenv("CKO_IBS_PORT", "8766"))
        url = f"http://{host}:{port}"
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
        logging.info("CKO KNX IBS Scanner startet lokal auf %s", url)
        config = uvicorn.Config(app, host=host, port=port, log_config=None, access_log=False)
        server = uvicorn.Server(config)
        set_shutdown_handler(lambda: setattr(server, "should_exit", True))
        server.run()
    except Exception:  # noqa: BLE001 - top-level crash reporter must catch startup failures
        error_text = traceback.format_exc()
        error_log_path = _runtime_dir() / "CKO-KNX-IBS-error.log"
        try:
            error_log_path.write_text(error_text, encoding="utf-8")
        except OSError:
            error_log_path = Path(os.getenv("TEMP", ".")) / "CKO-KNX-IBS-error.log"
            error_log_path.write_text(error_text, encoding="utf-8")
        logging.exception("CKO KNX IBS Scanner konnte nicht gestartet werden")
        _show_startup_error(f"Das Programm konnte nicht gestartet werden.\n\nFehlerprotokoll:\n{error_log_path}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
