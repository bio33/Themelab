"""Capability-based Android and Wear OS adapters; no invented provider bindings."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Protocol

from . import adb
from .pack_models import COMPONENT

QUERIES = {
    "android_appwidget": (
        "query-receivers",
        "android.appwidget.action.APPWIDGET_UPDATE",
    ),
    "wear_os_complication": (
        "query-services",
        "android.support.wearable.complications.ACTION_COMPLICATION_UPDATE_REQUEST",
    ),
    "wear_os_tile": ("query-services", "androidx.wear.tiles.action.BIND_TILE_PROVIDER"),
}


class DeviceAdapter(Protocol):
    def discover(self, serial: str) -> dict: ...
    def stage(self, serial: str, source: Path, pack_id: str, label: str) -> dict: ...


class AndroidAdapter:
    kind = "phone"

    def discover(self, serial):
        detail = adb.info(serial)
        errors = []
        try:
            features = str(adb.on_device(serial, "shell", "pm", "list", "features"))
            kind = "watch" if "android.hardware.type.watch" in features else "phone"
        except adb.AdbError as exc:
            kind = "unknown"
            errors.append(str(exc))
        try:
            launcher = str(
                adb.on_device(
                    serial,
                    "shell",
                    "cmd",
                    "package",
                    "resolve-activity",
                    "--brief",
                    "--components",
                    "-a",
                    "android.intent.action.MAIN",
                    "-c",
                    "android.intent.category.HOME",
                )
            )
        except adb.AdbError:
            launcher = "unknown"
        providers = {}
        for platform, (query, action) in QUERIES.items():
            if kind == "phone" and platform != "android_appwidget":
                continue
            if kind == "watch" and platform == "android_appwidget":
                continue
            try:
                output = str(
                    adb.on_device(
                        serial,
                        "shell",
                        "cmd",
                        "package",
                        query,
                        "--brief",
                        "--components",
                        "-a",
                        action,
                    )
                )
                if re.search(
                    r"unknown|error|exception|permission denial", output, re.I
                ):
                    raise adb.AdbError(
                        "Provider query unsupported by this Android build"
                    )
                components = sorted(
                    set(
                        line.strip()
                        for line in output.splitlines()
                        if COMPONENT.fullmatch(line.strip())
                    )
                )
                providers[platform] = {
                    "status": "queried",
                    "components": components,
                    "types_verified": False,
                }
            except adb.AdbError as exc:
                providers[platform] = {
                    "status": "unknown",
                    "components": [],
                    "types_verified": False,
                    "reason": str(exc),
                }
        return {
            **detail,
            "kind": kind,
            "adapter": "wear_os" if kind == "watch" else "android",
            "launcher": launcher,
            "providers": providers,
            "discovery_errors": errors,
            "apply_capabilities": {
                "file_transfer": True,
                "wallpaper_assignment": "user_action",
                "widget_placement": "user_action",
                "complication_assignment": "user_action",
                "tile_order": "user_action",
                "automatic_restore": False,
            },
        }

    def stage(self, serial, source, pack_id, label):
        # Remote names are generated from validated IDs, never untrusted archive paths.
        if not re.fullmatch(r"[a-f0-9]{32}", pack_id) or not re.fullmatch(
            r"[a-z0-9_-]+", label
        ):
            raise adb.AdbError("Invalid staging identity")
        suffix = source.suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            raise adb.AdbError("Invalid file suffix")
        folder = f"/sdcard/Download/ThemeLab/{pack_id}"
        remote = f"{folder}/{label}{suffix}"
        # Never remove/overwrite another user's file. An existing matching file is reusable.
        try:
            adb.on_device(serial, "shell", "ls", remote)
        except adb.AdbError as missing:
            if "no such file" not in str(missing).lower():
                raise
        else:
            try:
                output = str(adb.on_device(serial, "shell", "sha256sum", remote))
            except adb.AdbError as exc:
                raise adb.AdbError(
                    "Staging path exists but cannot be checksum-verified; refusing overwrite"
                ) from exc
            observed = output.split()[0] if output else ""
            if observed != hashlib.sha256(source.read_bytes()).hexdigest():
                raise adb.AdbError(
                    "Staging path already contains different or unverified content; refusing overwrite"
                )
            return {
                "serial": serial,
                "path": remote,
                "sha256": observed,
                "created_by_job": False,
                "transfer_verified": True,
            }
        adb.on_device(serial, "shell", "mkdir", "-p", folder)
        adb.on_device(serial, "push", str(source), remote, timeout=45)
        result = {
            "serial": serial,
            "path": remote,
            "created_by_job": True,
            "transfer_verified": False,
        }
        try:
            output = str(adb.on_device(serial, "shell", "sha256sum", remote))
            observed = output.split()[0] if output else ""
            if re.fullmatch(r"[a-f0-9]{64}", observed):
                if observed != hashlib.sha256(source.read_bytes()).hexdigest():
                    raise adb.AdbError("Staged file checksum mismatch")
                result.update(sha256=observed, transfer_verified=True)
        except adb.AdbError as exc:
            if "mismatch" in str(exc):
                raise
        if self.kind == "phone":
            try:
                adb.on_device(
                    serial,
                    "shell",
                    "am",
                    "broadcast",
                    "-a",
                    "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                    "-d",
                    "file://" + remote,
                )
            except adb.AdbError:
                pass
        return result

    def open_settings(self, serial):
        action = (
            "android.intent.action.SET_WALLPAPER"
            if self.kind == "phone"
            else "android.settings.SETTINGS"
        )
        output = str(adb.on_device(serial, "shell", "am", "start", "-a", action))
        if re.search(r"error|unable to resolve|exception", output, re.I):
            raise adb.AdbError(
                "Settings action unavailable; use the instructions on your device"
            )


class WearOSAdapter(AndroidAdapter):
    kind = "watch"


ADAPTERS: dict[str, DeviceAdapter] = {
    "phone": AndroidAdapter(),
    "watch": WearOSAdapter(),
}


def discover(serial):
    return AndroidAdapter().discover(serial)
