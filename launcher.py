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
from collections.abc import Callable
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


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


def _wait_until_ready(url: str) -> bool:
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1) as response:
                if response.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    return False


def _asset_path(filename: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / filename


def _application_is_running(url: str) -> bool:
    """Return true only when our local service already owns the configured port."""
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1) as response:
            return response.status == 200 and b'"local_only":true' in response.read().replace(b" ", b"")
    except (OSError, urllib.error.URLError):
        return False


class DesktopApi:
    """Small native bridge used only to close the desktop shell cleanly."""

    def __init__(self) -> None:
        self.window: Any | None = None

    def close_window(self) -> bool:
        if self.window is not None:
            self.window.destroy()
        return True


def _run_desktop_window(url: str, stop_server: Callable[[], None] | None = None) -> None:
    import webview

    api = DesktopApi()
    window = webview.create_window(
        "CKO KNX IBS Scanner",
        url=url,
        js_api=api,
        width=1540,
        height=960,
        min_size=(1120, 720),
        resizable=True,
        background_color="#091321",
        text_select=True,
    )
    api.window = window
    if stop_server is not None:
        window.events.closed += stop_server
    webview.start(
        gui="edgechromium",
        debug=False,
        private_mode=False,
        storage_path=str(Path(os.getenv("LOCALAPPDATA", _runtime_dir())) / "CKO" / "KNX-IBS"),
        icon=str(_asset_path("cko-toolbox.ico")),
    )


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
        if _application_is_running(url):
            LOGGER.info("Bereits laufende Instanz erkannt. Öffne vorhandene Oberfläche im WebView.")
            _run_desktop_window(url)
            return
        LOGGER.info("CKO KNX IBS Scanner startet lokal auf %s", url)
        config = uvicorn.Config(app, host=host, port=port, log_config=None, access_log=False)
        server = uvicorn.Server(config)
        set_shutdown_handler(lambda: setattr(server, "should_exit", True))
        server_thread = threading.Thread(target=server.run, name="cko-ibs-server", daemon=True)
        server_thread.start()
        if not _wait_until_ready(url):
            raise RuntimeError("Der lokale Anwendungsdienst konnte nicht gestartet werden.")

        def stop_server() -> None:
            server.should_exit = True

        _run_desktop_window(url, stop_server)
        server.should_exit = True
        server_thread.join(timeout=8)
    except Exception:
        error_text = traceback.format_exc()
        error_log_path = _runtime_dir() / "CKO-KNX-IBS-error.log"
        try:
            error_log_path.write_text(error_text, encoding="utf-8")
        except OSError:
            error_log_path = Path(os.getenv("TEMP", ".")) / "CKO-KNX-IBS-error.log"
            error_log_path.write_text(error_text, encoding="utf-8")
        LOGGER.exception("CKO KNX IBS Scanner konnte nicht gestartet werden")
        _show_startup_error(f"Das Programm konnte nicht gestartet werden.\n\nFehlerprotokoll:\n{error_log_path}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
