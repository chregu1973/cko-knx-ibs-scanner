import time

from launcher import DesktopApi


class FakeWindow:
    def __init__(self) -> None:
        self.destroyed = 0

    def destroy(self) -> None:
        self.destroyed += 1


def test_bridge_exposes_no_public_state_to_pywebview() -> None:
    # pywebview exponiert öffentliche Attribute der js_api; das Fensterobjekt darf nicht dabei sein
    api = DesktopApi()
    api.attach(FakeWindow())
    assert [name for name in vars(api) if not name.startswith("_")] == []


def test_close_window_destroys_once_even_if_called_twice() -> None:
    api = DesktopApi()
    window = FakeWindow()
    api.attach(window)
    assert api.close_window() is True
    assert api.close_window() is True  # JavaScript-Brücke und Python-Weg dürfen beide auslösen
    time.sleep(0.4)
    api.mark_closed()  # sonst würde der Watchdog den Testprozess beenden
    assert window.destroyed == 1
