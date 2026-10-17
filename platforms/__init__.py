"""The overlay's OS-specific parts behind one interface (base.Platform), one implementation
per OS. create_platform() builds the one for the OS it's running on; only that OS's module is
imported, so one OS's libraries are never loaded on another."""
import sys

from .base import Hotkeys, Platform, hotkey_text, parse_hotkey

__all__ = ["Hotkeys", "Platform", "create_platform", "hotkey_text", "parse_hotkey"]


def create_platform(name=sys.platform):
    """The Platform for this OS (sys.platform's name for it)."""
    if name == "win32":
        from .windows import WindowsPlatform
        return WindowsPlatform()
    raise RuntimeError(f"Maple Classic Exp Overlay runs on Windows, not {name}.")
