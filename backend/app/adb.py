"""Narrow, device-scoped wrappers around the official Android Debug Bridge CLI."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


class AdbError(RuntimeError):
    pass


def adb_binary() -> str:
    override = os.getenv("ADB_PATH")
    if override and Path(override).is_file():
        return str(Path(override))
    binary = shutil.which("adb")
    if binary:
        return binary
    project_root = Path(__file__).resolve().parents[2]
    candidates = [project_root / "tools" / "platform-tools" / "adb.exe",
                  project_root / "tools" / "platform-tools" / "adb"]
    for path in candidates:
        if path.is_file():
            return str(path)
    raise AdbError("ADB not found. Install Android SDK Platform-Tools and put adb on PATH, or set ADB_PATH.")


def run_adb(*args: str, timeout: int = 15, binary: bool = False) -> bytes | str:
    """Always use argv, never a shell; raise errors to API handlers."""
    argv = [adb_binary(), *args]
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"ADB timed out after {timeout}s") from exc
    except OSError as exc:
        raise AdbError(f"ADB could not be started: {exc}") from exc
    if completed.returncode != 0:
        msg = completed.stderr.decode("utf-8", "replace").strip() or completed.stdout.decode("utf-8", "replace").strip()
        raise AdbError(msg[:500] or f"ADB exited with {completed.returncode}")
    return completed.stdout if binary else completed.stdout.decode("utf-8", "replace").strip()


def connected_devices() -> list[dict[str, str]]:
    output = str(run_adb("devices", "-l"))
    result: list[dict[str, str]] = []
    for line in output.splitlines()[1:]:
        cols = line.strip().split()
        if len(cols) < 2 or cols[0].startswith("*"):
            continue
        serial, status = cols[:2]
        extra = {k: v for x in cols[2:] if ":" in x for k, v in [x.split(":", 1)]}
        result.append({"serial": serial, "status": status,
                       "model_hint": extra.get("model", ""), "product_hint": extra.get("product", "")})
    return result


def require_device(serial: str) -> dict[str, str]:
    devices = connected_devices()
    target = next((dev for dev in devices if dev["serial"] == serial), None)
    if not target:
        raise AdbError("Device is not connected. Refresh the device list.")
    if target["status"] != "device":
        raise AdbError(f"Device status is {target['status']}. Unlock the phone and authorize USB debugging.")
    return target


def on_device(serial: str, *args: str, timeout: int = 15, binary: bool = False) -> bytes | str:
    require_device(serial)
    return run_adb("-s", serial, *args, timeout=timeout, binary=binary)


def getprop(serial: str, key: str) -> str:
    return str(on_device(serial, "shell", "getprop", key))


def info(serial: str) -> dict[str, Any]:
    device = require_device(serial)
    manufacturer = getprop(serial, "ro.product.manufacturer")
    model = getprop(serial, "ro.product.model") or device.get("model_hint", "")
    android = getprop(serial, "ro.build.version.release")
    api_level = getprop(serial, "ro.build.version.sdk")
    resolution = str(on_device(serial, "shell", "wm", "size"))
    battery = str(on_device(serial, "shell", "dumpsys", "battery"))
    charge = re.search(r"^\s*level:\s*(\d+)", battery, re.MULTILINE)
    from .profiles import capabilities
    return {**device, "manufacturer": manufacturer, "model": model, "android": android,
            "api_level": api_level, "resolution": resolution.splitlines()[-1].removeprefix("Physical size: "),
            "battery_percent": int(charge.group(1)) if charge else None,
            "capabilities": capabilities(manufacturer)}
