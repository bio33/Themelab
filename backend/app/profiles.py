"""Device profiles. Extend by manufacturer without changing ADB or HTTP layer."""
from __future__ import annotations


def capabilities(manufacturer: str) -> dict[str, object]:
    samsung = "samsung" in manufacturer.lower()
    return {
        "profile": "samsung_one_ui" if samsung else "generic_android",
        "adb_file_transfer": True,
        "adb_screen_capture": True,
        "wallpaper_picker": True,
        "static_wallpaper_one_click": False,  # Requires our own Android companion app.
        "live_wallpaper_one_click": False,  # Android confirmation / engine needed.
        "theme_icons_one_click": False,  # Depends on launcher or OEM theme services.
        "samsung_good_lock": samsung,
    }
