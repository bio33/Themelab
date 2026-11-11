"""Theme-pack API, alongside the original MVP API."""

from __future__ import annotations

import io
import json
import re
import zipfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

from . import device_adapters
from .pack_import import (
    MAX_ZIP,
    SCHEMA,
    PackError,
    asset_records,
    import_archive,
    safe_path,
)
from .pack_models import Acknowledge, PlanInput, Selection, StartJob
from .pack_store import Store, LOCK
from . import pack_service as service

router = APIRouter(prefix="/api/packs", tags=["theme packs"])


def get_store():
    from .main import PACKS_ROOT

    return Store(PACKS_ROOT)


def checked_pack(store, key):
    if not re.fullmatch(r"[a-f0-9]{32}", key):
        raise HTTPException(404, "Theme pack not found")
    try:
        return store.pack(key)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("")
def list_packs(store: Store = Depends(get_store)):
    return {"packs": store.packs()}


@router.get("/schema")
def schema():
    return SCHEMA


@router.get("/devices/{serial}/capabilities")
def capabilities(serial: str):
    return device_adapters.discover(serial)


@router.post("/import", status_code=201)
async def upload(file: UploadFile = File(), store: Store = Depends(get_store)):
    raw = await file.read(MAX_ZIP + 1)
    if len(raw) > MAX_ZIP:
        raise HTTPException(413, "ZIP exceeds 256 MB")
    return {"packs": await run_in_threadpool(import_archive, store, raw)}


@router.post("/migrate-legacy", status_code=201)
def migrate_existing(store: Store = Depends(get_store)):
    from .main import THEMES_ROOT, ID_PATTERN

    results = []
    for folder in THEMES_ROOT.iterdir():
        if (
            folder.is_dir()
            and ID_PATTERN.fullmatch(folder.name)
            and (folder / "manifest.json").is_file()
        ):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for path in folder.rglob("*"):
                    if path.is_symlink():
                        raise PackError(
                            "Legacy theme contains a symlink; export a clean pack"
                        )
                    if path.is_file():
                        archive.write(path, path.relative_to(folder))
            results.extend(import_archive(store, buffer.getvalue()))
    return {"packs": results}


@router.get("/jobs")
def jobs(store: Store = Depends(get_store)):
    return {
        "jobs": sorted(store.all("job"), key=lambda v: v["created_at"], reverse=True)
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str, store: Store = Depends(get_store)):
    value = store.get("job", job_id)
    if value is None:
        raise HTTPException(404, "Apply job not found")
    return value


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, store: Store = Depends(get_store)):
    return service.cancel_job(store, job_id)


@router.post("/jobs/{job_id}/retry")
def retry(job_id: str, store: Store = Depends(get_store)):
    return service.retry_job(store, job_id)


@router.post("/jobs/{job_id}/cleanup-staging")
def cleanup(job_id: str, store: Store = Depends(get_store)):
    return service.cleanup_staging(store, job_id)


@router.post("/jobs/{job_id}/items/{item_id}/confirm")
def confirm(
    job_id: str, item_id: str, request: Acknowledge, store: Store = Depends(get_store)
):
    return service.acknowledge(
        store, job_id, item_id, request.outcome, request.confirmed_on_device
    )


@router.get("/{pack_id}")
def get_pack(pack_id: str, store: Store = Depends(get_store)):
    pack = checked_pack(store, pack_id)
    selection = store.get("selection", pack_id, service.default_selection(pack))
    return {
        **pack,
        "selection": selection,
        "components": service.effective(pack, selection),
    }


@router.get("/{pack_id}/assets/{path:path}")
def get_asset(pack_id: str, path: str, store: Store = Depends(get_store)):
    pack = checked_pack(store, pack_id)
    record = next(
        (r for r in asset_records(pack["manifest"]) if r["path"] == path), None
    )
    if not record:
        raise HTTPException(404, "Asset not referenced by this theme")
    file = safe_path(store.assets / pack_id, path)
    if not file.is_file():
        raise HTTPException(404, "Asset not found")
    return FileResponse(
        file,
        media_type=record["mime_type"],
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'; form-action 'none'",
        },
    )


@router.get("/{pack_id}/export")
def export(pack_id: str, store: Store = Depends(get_store)):
    pack = checked_pack(store, pack_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(pack["manifest"], indent=2))
        paths = sorted({r["path"] for r in asset_records(pack["manifest"])})
        for path in paths:
            if path == "manifest.json":
                raise PackError("Asset conflicts with manifest path")
            archive.write(safe_path(store.assets / pack_id, path), path)
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="theme-{pack_id}.zip"'},
    )


@router.get("/{pack_id}/selection")
def selection(pack_id: str, store: Store = Depends(get_store)):
    pack = checked_pack(store, pack_id)
    return store.get("selection", pack_id, service.default_selection(pack))


@router.post("/{pack_id}/selection")
def update_selection(
    pack_id: str, request: Selection, store: Store = Depends(get_store)
):
    checked_pack(store, pack_id)
    return service.save_selection(store, pack_id, request.model_dump())


@router.post("/{pack_id}/selection/reset")
def reset_selection(pack_id: str, store: Store = Depends(get_store)):
    pack = checked_pack(store, pack_id)
    with LOCK:
        store.put("selection", pack_id, service.default_selection(pack))
    return store.get("selection", pack_id)


@router.post("/{pack_id}/plan")
def plan(pack_id: str, request: PlanInput, store: Store = Depends(get_store)):
    checked_pack(store, pack_id)
    return service.make_plan(store, pack_id, request.devices)


@router.post("/{pack_id}/jobs", status_code=202)
def apply(pack_id: str, request: StartJob, store: Store = Depends(get_store)):
    checked_pack(store, pack_id)
    return service.start_job(store, pack_id, request.plan_id)
