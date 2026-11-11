"""Local-only API for the Android Customization Studio MVP."""
from __future__ import annotations

import io
import json
import re
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, UnidentifiedImageError

from . import adb

ROOT = Path(__file__).resolve().parents[2]
THEMES_ROOT = ROOT / "data" / "themes"
PACKS_ROOT = ROOT / "data" / "packs"
THEMES_ROOT.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = 25 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000
ID_PATTERN = re.compile(r"^[a-f0-9]{12}$")

@asynccontextmanager
async def lifespan(_):
    from .pack_service import recover_jobs
    from .pack_store import Store
    recover_jobs(Store(PACKS_ROOT))
    yield

app = FastAPI(title="ThemeLab", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"], allow_headers=["*"], allow_credentials=False
)

from .pack_import import PackError
from .pack_routes import router as pack_router
app.include_router(pack_router)

@app.exception_handler(PackError)
async def pack_exception_handler(_, exc):
    return Response(content=json.dumps({"detail":str(exc)}),status_code=422,media_type="application/json")

@app.exception_handler(KeyError)
async def missing_exception_handler(_, exc):
    return Response(content=json.dumps({"detail":str(exc)}),status_code=404,media_type="application/json")


class ThemeInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@app.exception_handler(adb.AdbError)
async def adb_exception_handler(_, exc: adb.AdbError):
    return Response(content=json.dumps({"detail": str(exc)}), status_code=400, media_type="application/json")


def theme_dir(theme_id: str) -> Path:
    if not ID_PATTERN.fullmatch(theme_id):
        raise HTTPException(404, "Theme not found")
    folder = THEMES_ROOT / theme_id
    if not folder.is_dir():
        raise HTTPException(404, "Theme not found")
    return folder


def load_theme(theme_id: str) -> dict:
    folder = theme_dir(theme_id)
    return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))


@app.get("/api/health")
def health():
    try:
        adb_path = adb.adb_binary()
        version = str(adb.run_adb("version")).splitlines()[0]
        import shutil
        return {"ok": True, "adb": True, "adb_path": adb_path, "adb_version": version,
                "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe"))}
    except adb.AdbError as exc:
        import shutil
        return {"ok": True, "adb": False, "error": str(exc),
                "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe"))}


@app.get("/api/devices")
def devices():
    return {"devices": adb.connected_devices()}


@app.get("/api/devices/{serial}")
def device_info(serial: str):
    return adb.info(serial)


@app.get("/api/devices/{serial}/screenshot")
def screenshot(serial: str):
    data = adb.on_device(serial, "exec-out", "screencap", "-p", binary=True, timeout=25)
    if not isinstance(data, bytes) or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(502, "Device did not return a PNG screenshot")
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/themes")
def themes():
    result = []
    for path in sorted(THEMES_ROOT.iterdir(), reverse=True):
        if path.is_dir() and ID_PATTERN.fullmatch(path.name) and (path / "manifest.json").is_file():
            result.append(load_theme(path.name))
    return {"themes": result}


@app.post("/api/themes", status_code=201)
def create_theme(request: ThemeInput):
    theme_id = uuid.uuid4().hex[:12]
    folder = THEMES_ROOT / theme_id
    folder.mkdir(parents=True)
    theme = {"id": theme_id, "name": request.name.strip(), "created_at": datetime.now(timezone.utc).isoformat(),
             "wallpaper": None}
    if not theme["name"]:
        raise HTTPException(422, "Theme name cannot be blank")
    (folder / "manifest.json").write_text(json.dumps(theme, indent=2), encoding="utf-8")
    return theme


@app.post("/api/themes/{theme_id}/wallpaper")
async def upload_wallpaper(theme_id: str, file: Annotated[UploadFile, File()]):
    folder = theme_dir(theme_id)
    if file.content_type not in ("image/png", "image/jpeg", "image/webp", "image/bmp", "application/octet-stream"):
        raise HTTPException(415, "Upload a PNG, JPEG, WebP, or BMP image")
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "Maximum wallpaper file size is 25 MB")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ("PNG", "JPEG", "WEBP", "BMP"):
                raise HTTPException(415, "Invalid wallpaper format")
            converted = ImageOps.exif_transpose(source).convert("RGB")
            width, height = converted.size
            if width < 300 or height < 300:
                raise HTTPException(422, "Wallpaper image is too small")
            converted.save(folder / "wallpaper.png", optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "The image cannot be decoded safely") from exc
    theme = load_theme(theme_id)
    theme["wallpaper"] = {"filename": "wallpaper.png", "width": width, "height": height}
    (folder / "manifest.json").write_text(json.dumps(theme, indent=2), encoding="utf-8")
    return theme


@app.get("/api/themes/{theme_id}/wallpaper")
def get_wallpaper(theme_id: str):
    file = theme_dir(theme_id) / "wallpaper.png"
    if not file.is_file():
        raise HTTPException(404, "This theme has no wallpaper")
    return FileResponse(file, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/devices/{serial}/stage-wallpaper/{theme_id}")
def stage_wallpaper(serial: str, theme_id: str):
    image = theme_dir(theme_id) / "wallpaper.png"
    if not image.is_file():
        raise HTTPException(422, "Upload a wallpaper first")
    adb.require_device(serial)
    remote_dir = "/sdcard/Pictures/ThemeLab"
    remote_file = f"{remote_dir}/{theme_id}.png"
    adb.on_device(serial, "shell", "mkdir", "-p", remote_dir)
    adb.on_device(serial, "push", str(image), remote_file, timeout=45)
    # Best-effort media indexing; some modern OEM builds ignore this legacy broadcast.
    try:
        adb.on_device(serial, "shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                      "-d", f"file://{remote_file}")
    except adb.AdbError:
        pass
    return {"ok": True, "remote_file": remote_file,
            "note": "Wallpaper copied to Pictures/ThemeLab. Select it from Samsung Gallery or the wallpaper picker; not applied automatically."}


@app.post("/api/devices/{serial}/open-wallpaper-picker")
def open_wallpaper_picker(serial: str):
    output = str(adb.on_device(serial, "shell", "am", "start", "-a", "android.intent.action.SET_WALLPAPER"))
    if "Error" in output or "unable to resolve" in output.lower():
        raise HTTPException(409, "Wallpaper picker unavailable on this phone; open Settings > Wallpaper manually.")
    return {"ok": True, "note": "Opened the wallpaper picker. Choose the staged image and confirm on your phone."}
