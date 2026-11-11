"""Bounded, transactional ZIP ingestion and content-based media verification."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
import warnings
import zipfile
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator
from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError

from .pack_models import Manifest
from .pack_store import Store, LOCK, now

MAX_ZIP = 256 * 1024 * 1024
MAX_EXPANDED = 768 * 1024 * 1024
MAX_FILE = 128 * 1024 * 1024
MAX_ENTRIES = 1024
SCHEMA = json.loads((Path(__file__).parent / "schemas/theme.schema.json").read_text())


class PackError(ValueError):
    pass


def safe_path(root: Path, value: str) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or any(ord(c) < 32 for c in value)
    ):
        raise PackError("Invalid relative asset path")
    parts = PurePosixPath(value)
    if parts.is_absolute() or any(p in ("..", ".", "") for p in value.split("/")):
        raise PackError("Asset path must stay inside its theme directory")
    if len(value) > 240 or any(
        p.endswith((" ", "."))
        or re.fullmatch(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p, re.I)
        for p in parts.parts
    ):
        raise PackError("Asset path is not portable to Windows")
    result = root.joinpath(*parts.parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise PackError("Asset symlink escapes its theme directory")
    return result


def strict_json(raw: bytes):
    if len(raw) > 1024 * 1024:
        raise PackError("Manifest exceeds 1 MB")

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise PackError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                PackError("Invalid JSON number")
            ),
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PackError("Invalid JSON manifest: " + str(exc)) from exc


def asset_records(node):
    if isinstance(node, dict):
        if "path" in node and "mime_type" in node:
            yield node
        else:
            for value in node.values():
                yield from asset_records(value)
    elif isinstance(node, list):
        for value in node:
            yield from asset_records(value)


def probe_video(path: Path):
    binary = os.getenv("FFPROBE_PATH") or shutil.which("ffprobe")
    decoder = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg")
    if not binary or not decoder:
        raise PackError(
            "Install FFmpeg (ffmpeg and ffprobe on PATH), or set FFMPEG_PATH / FFPROBE_PATH, to verify phone live wallpapers"
        )

    def call(argv, timeout=45):
        try:
            return subprocess.run(
                argv, capture_output=True, timeout=timeout, check=True
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise PackError("Video cannot be decoded safely") from exc

    try:
        meta = json.loads(
            call(
                [
                    binary,
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                15,
            )
        )
        streams = meta["streams"]
        videos = [s for s in streams if s.get("codec_type") == "video"]
        if len(videos) != 1 or any(s.get("codec_type") == "audio" for s in streams):
            raise PackError(
                "Phone live wallpaper must have one H.264 video stream and no audio"
            )
        v = videos[0]
        duration = float(meta["format"]["duration"])
        numerator, denominator = map(float, v["avg_frame_rate"].split("/"))
        fps = numerator / denominator
        if v["codec_name"] != "h264" or "mp4" not in meta["format"]["format_name"]:
            raise PackError(
                "Phone live wallpaper must contain H.264 in an MP4 container"
            )
        if (
            not 0 < duration <= 30
            or not 1 <= fps <= 60
            or v["width"] * v["height"] > 12_000_000
        ):
            raise PackError("Video exceeds limits: 30 seconds, 60 fps, 12 megapixels")
        call(
            [
                decoder,
                "-v",
                "error",
                "-xerror",
                "-threads",
                "2",
                "-i",
                str(path),
                "-f",
                "null",
                "-",
            ]
        )
        frames = []
        for t in (0, duration * 0.25, duration * 0.5):
            raw = call(
                [
                    decoder,
                    "-v",
                    "error",
                    "-threads",
                    "2",
                    "-ss",
                    str(t),
                    "-i",
                    str(path),
                    "-vf",
                    "scale=64:64",
                    "-frames:v",
                    "1",
                    "-threads",
                    "2",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-",
                ]
            )
            frames.append(Image.frombytes("RGB", (64, 64), raw))
        if all(
            sum(ImageStat.Stat(ImageChops.difference(frames[0], f)).mean) < 0.3
            for f in frames[1:]
        ):
            raise PackError(
                "Live wallpaper appears static; provide visibly animated content"
            )
        return dict(
            width=v["width"], height=v["height"], duration_seconds=duration, fps=fps
        )
    except (KeyError, ZeroDivisionError, TypeError, ValueError) as exc:
        if isinstance(exc, PackError):
            raise
        raise PackError("Invalid video stream metadata") from exc


def inspect_image(path: Path, required_animation=False):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                fmt = image.format
                if fmt not in ("PNG", "JPEG", "WEBP", "GIF", "BMP"):
                    raise PackError("Unsupported image format")
                frames = getattr(image, "n_frames", 1)
                if (
                    image.width * image.height > 12_000_000
                    or frames * image.width * image.height * 4 > 96 * 1024 * 1024
                ):
                    raise PackError(
                        "Image/animation exceeds decoded memory budget (96 MB)"
                    )
                if required_animation and (
                    fmt not in ("WEBP", "GIF")
                    or frames < 2
                    or image.info.get("loop") != 0
                ):
                    raise PackError(
                        "Watch live background must be an infinitely looping animated WebP or GIF"
                    )
                first = None
                moving = False
                duration = 0
                for i in range(frames):
                    image.seek(i)
                    image.load()
                    duration += image.info.get("duration", 0)
                    thumb = image.convert("RGB").resize((64, 64))
                    if first is None:
                        first = thumb
                    elif ImageChops.difference(first, thumb).getbbox():
                        moving = True
                if required_animation and (not moving or not 0 < duration <= 30_000):
                    raise PackError(
                        "Watch animation must move and have a duration of at most 30 seconds"
                    )
                result = dict(width=image.width, height=image.height)
                if frames > 1:
                    result.update(
                        duration_seconds=duration / 1000, fps=frames / (duration / 1000)
                    )
                return fmt, result
    except (
        UnidentifiedImageError,
        OSError,
        EOFError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise PackError("Image cannot be decoded safely") from exc


def validate_media(manifest, folder, legacy=False):
    issues = []
    required = [
        (manifest.get("phone", {}).get("live_wallpaper"), "Phone live wallpaper"),
        (manifest.get("watch", {}).get("live_background"), "Watch live background"),
    ]
    for asset, label in required:
        if not asset:
            if legacy:
                issues.append(label + " is missing")
            else:
                raise PackError(label + " is required")
    phone = required[0][0]
    watch = required[1][0]
    fingerprints = {}
    for record in asset_records(manifest):
        path = safe_path(folder, record["path"])
        if not path.is_file():
            raise PackError("Referenced asset is missing: " + record["path"])
        if path.stat().st_size > MAX_FILE:
            raise PackError("Referenced asset exceeds file limit")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if record.get("sha256") and checksum != record["sha256"]:
            raise PackError("Checksum mismatch: " + record["path"])
        fingerprints[record["path"]] = checksum
        mime = record["mime_type"]
        observed = {}
        if mime == "video/mp4":
            observed = probe_video(path)
        elif mime.startswith("image/"):
            fmt, observed = inspect_image(path, record is watch)
            actual_mime = {
                "PNG": "image/png",
                "JPEG": "image/jpeg",
                "WEBP": "image/webp",
                "GIF": "image/gif",
                "BMP": "image/bmp",
            }[fmt]
            if actual_mime != mime:
                raise PackError("Image content does not match declared MIME type")
        if record is phone and mime != "video/mp4":
            raise PackError("Phone live wallpaper must be MP4")
        if record is watch and mime not in ("image/webp", "image/gif"):
            raise PackError("Watch live background must be animated WebP or GIF")
        for key, actual in observed.items():
            declared = record.get(key)
            tolerance = (
                max(0.2, actual * 0.1) if key in ("duration_seconds", "fps") else 0
            )
            if declared is not None and abs(actual - declared) > tolerance:
                raise PackError("Incorrect " + key + " for " + record["path"])
    return issues, fingerprints


def validate_manifest(data, legacy=False):
    version = data.get("schema_version")
    if not isinstance(version, str) or not re.fullmatch(r"1\.\d+\.\d+", version):
        raise PackError("Unsupported schema version; supported major version is 1")
    schema = copy.deepcopy(SCHEMA)
    schema["properties"]["schema_version"] = {
        "type": "string",
        "pattern": r"^1\.\d+\.\d+$",
    }
    if legacy:
        schema["properties"]["phone"]["required"] = []
        schema["properties"]["watch"]["required"] = []
    errors = sorted(
        Draft202012Validator(schema).iter_errors(data), key=lambda e: str(e.path)
    )
    if errors:
        error = errors[0]
        raise PackError(
            "Manifest "
            + ".".join(map(str, error.absolute_path))
            + ": "
            + error.message[:250]
        )
    parsed = Manifest.model_validate(data)
    if any(
        not re.fullmatch(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+", key)
        for key in parsed.extensions
    ):
        raise PackError(
            "Extension keys must be namespaced, for example org.example.settings"
        )
    ids = [
        c.id
        for c in parsed.phone.widgets
        + parsed.watch.tiles
        + parsed.watch.complication_slots
    ]
    reserved = {"phone-live", "watch-live"} | {
        f"{dev}-{k}".replace("_", "-")
        for dev in ("phone", "watch")
        for k in (
            "static_wallpaper",
            "static_background",
            "layout",
            "icons",
            "typography",
            "lock_screen",
            "sounds",
            "system_style",
            "clock",
            "aod",
            "face_package",
        )
    }
    if len(set(ids)) != len(ids) or set(ids) & reserved:
        raise PackError(
            "Component IDs must be unique and cannot use reserved built-in IDs"
        )
    slot_ids = set()
    for slot in parsed.watch.complication_slots:
        b = slot.bounds
        if (
            slot.slot_id in slot_ids
            or b.x + b.width > 1
            or b.y + b.height > 1
            or b.width <= 0
            or b.height <= 0
        ):
            raise PackError("Invalid or duplicate complication slot bounds/ID")
        slot_ids.add(slot.slot_id)
        if (
            slot.default_category not in {option.category for option in slot.options}
            or "EMPTY" not in slot.supported_types
        ):
            raise PackError(
                "Complication needs a valid default category and EMPTY support"
            )
    return data


def migrate(data, folder: Path):
    """Normalize known MVP and classic-collection formats; never fabricate motion."""
    theme_id = str(data.get("id", "legacy-theme"))
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", theme_id):
        theme_id = "legacy-" + re.sub(r"[^a-z0-9_-]", "-", theme_id.lower())[:80]
    palette = {
        k: data.get(k, default)
        for k, default in [
            ("background", "#111827"),
            ("foreground", "#FFFFFF"),
            ("accent", "#986AF7"),
        ]
    }
    result = dict(
        schema_version="1.0.0",
        theme=dict(
            id=theme_id,
            title=data.get("title") or data.get("name") or "Imported theme",
            version=str(data.get("version", "1.0.0-legacy")),
            palette=palette,
        ),
        generation_policy=dict(
            phone_live_required=True,
            watch_live_required=True,
            fetch_missing_optional_components=False,
        ),
        phone={},
        watch={},
    )

    def record(path, mime, live=False):
        file = safe_path(folder, path)
        if not file.is_file():
            return None
        if mime == "video/mp4":
            metadata = probe_video(file)
        else:
            _, metadata = inspect_image(file, live)
        out = dict(
            path=path,
            mime_type=mime,
            sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
            **metadata,
        )
        if live:
            out.update(loop=True, audio=False)
        return out

    if data.get("format") == "classic-theme-assets-v1":
        for dev, field, filename, mime in [
            ("phone", "live_wallpaper", "live-wallpaper.mp4", "video/mp4"),
            ("watch", "live_background", "live-background.webp", "image/webp"),
        ]:
            value = record(dev + "/" + filename, mime, True)
            if value:
                result[dev][field] = value
        for dev, field in [
            ("phone", "static_wallpaper"),
            ("watch", "static_background"),
        ]:
            value = record(dev + "/wallpaper.png", "image/png")
            if value:
                result[dev][field] = value
        example = (
            Path(__file__).resolve().parents[2]
            / "docs/theme-packs/examples"
            / (theme_id + ".theme.json")
        )
        if example.is_file():
            suggested = json.loads(example.read_text())
            for field in ("widgets", "layout"):
                if field in suggested["phone"]:
                    result["phone"][field] = suggested["phone"][field]
            for field in ("complication_slots", "tiles", "clock", "aod"):
                if field in suggested["watch"]:
                    result["watch"][field] = suggested["watch"][field]
    elif "name" in data and "wallpaper" in data:
        filename = (data.get("wallpaper") or {}).get("filename", "wallpaper.png")
        value = record(filename, "image/png")
        if value:
            result["phone"]["static_wallpaper"] = value
    else:
        raise PackError("Unrecognized legacy theme manifest")
    return result


def import_archive(store: Store, raw: bytes):
    if len(raw) > MAX_ZIP:
        raise PackError("ZIP exceeds 256 MB")
    try:
        with tempfile.TemporaryDirectory(prefix="import-", dir=store.root) as temp:
            root = Path(temp)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if (
                    len(entries) > MAX_ENTRIES
                    or sum(i.file_size for i in entries) > MAX_EXPANDED
                ):
                    raise PackError("Archive exceeds 1024 entries or 768 MB expanded")
                names = set()
                for entry in entries:
                    name = entry.filename.rstrip("/")
                    target = safe_path(root, name)
                    mode = entry.external_attr >> 16
                    if stat.S_ISLNK(mode) or (
                        stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                    ):
                        raise PackError(
                            "Archive symlinks and special files are not accepted"
                        )
                    if name.casefold() in names:
                        raise PackError("Duplicate or case-conflicting archive path")
                    names.add(name.casefold())
                    if (
                        entry.flag_bits & 1
                        or entry.file_size > MAX_FILE
                        or entry.file_size > max(1, entry.compress_size) * 250
                    ):
                        raise PackError(
                            "Encrypted, oversized or excessively compressed archive entry"
                        )
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, target.open("xb") as dest:
                        copied = 0
                        while chunk := source.read(1024 * 1024):
                            copied += len(chunk)
                            if copied > entry.file_size or copied > MAX_FILE:
                                raise PackError("Archive size mismatch")
                            dest.write(chunk)
            # Prefer the standard manifest in each theme directory, preserving collection ZIPs.
            choices = {}
            for filename in ("theme.json", "manifest.json", "theme.standard.json"):
                for path in root.rglob(filename):
                    choices[path.parent] = path
            if not choices or len(choices) > 30:
                raise PackError("ZIP must contain 1–30 theme manifests")
            folders = list(choices)
            if any(a != b and a.is_relative_to(b) for a in folders for b in folders):
                raise PackError("Nested overlapping theme directories are ambiguous")
            pending = []
            for folder, path in choices.items():
                data = strict_json(path.read_bytes())
                if not isinstance(data, dict):
                    raise PackError("Manifest must be an object")
                legacy = "schema_version" not in data
                if legacy:
                    data = migrate(data, folder)
                validate_manifest(data, legacy)
                issues, fingerprints = validate_media(data, folder, legacy)
                digest = hashlib.sha256(
                    json.dumps([data, fingerprints], sort_keys=True).encode()
                ).hexdigest()
                pending.append(
                    dict(
                        id=uuid.uuid4().hex,
                        manifest=data,
                        issues=issues,
                        source="legacy" if legacy else "standard",
                        digest=digest,
                        folder=folder,
                    )
                )
            if len(
                {
                    (p["manifest"]["theme"]["id"], p["manifest"]["theme"]["version"])
                    for p in pending
                }
            ) != len(pending):
                raise PackError("Duplicate theme ID/version in archive")
            created = []
            results = []
            with LOCK:
                try:
                    with store.connection() as db:
                        for p in pending:
                            identity = p["manifest"]["theme"]
                            existing = store.find(identity["id"], identity["version"])
                            if existing:
                                if existing["digest"] != p["digest"]:
                                    raise PackError(
                                        "Theme ID/version already exists with different content; increment its version"
                                    )
                                results.append(existing["id"])
                                continue
                            dest = store.assets / p["id"]
                            shutil.copytree(p["folder"], dest)
                            created.append(dest)
                            db.execute(
                                "INSERT INTO packs VALUES(?,?,?,?,?,?,?,?)",
                                (
                                    p["id"],
                                    identity["id"],
                                    identity["version"],
                                    p["digest"],
                                    json.dumps(p["manifest"]),
                                    json.dumps(p["issues"]),
                                    p["source"],
                                    now(),
                                ),
                            )
                            results.append(p["id"])
                except Exception:
                    for folder in created:
                        shutil.rmtree(folder, ignore_errors=True)
                    raise
            return [store.pack(key) for key in results]
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise PackError("ZIP could not be imported: " + str(exc)[:200]) from exc
