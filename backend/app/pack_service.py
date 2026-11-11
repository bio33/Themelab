"""Selection resolution, immutable apply plans and resumable per-item jobs."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import adb, device_adapters, provider_matching
from .pack_import import PackError, safe_path, asset_records
from .pack_models import Selection, ApplyItem
from .pack_store import Store, LOCK, now

EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="theme-apply")


def components(manifest):
    result = []
    for device, field, label in [
        ("phone", "live_wallpaper", "Phone live wallpaper"),
        ("watch", "live_background", "Watch live background"),
    ]:
        value = manifest[device].get(field)
        if value:
            result.append(
                dict(
                    id=device + "-live",
                    device=device,
                    category="live",
                    label=label,
                    default_enabled=True,
                    asset=value["path"],
                    required=True,
                )
            )
    for c in manifest["phone"].get("widgets", []):
        result.append({**c, "device": "phone"})
    for c in manifest["watch"].get("tiles", []):
        result.append({**c, "device": "watch", "category": "tile"})
    for c in manifest["watch"].get("complication_slots", []):
        result.append(
            {
                **c,
                "device": "watch",
                "category": "complication",
                "label": "Complication slot " + str(c["slot_id"]),
            }
        )
    for device, fields in [
        (
            "phone",
            [
                "static_wallpaper",
                "layout",
                "icons",
                "typography",
                "lock_screen",
                "sounds",
                "system_style",
            ],
        ),
        ("watch", ["static_background", "clock", "aod", "face_package"]),
    ]:
        for field in fields:
            value = manifest[device].get(field)
            if value:
                result.append(
                    dict(
                        id=(device + "-" + field).replace("_", "-"),
                        device=device,
                        category=field,
                        label=field.replace("_", " ").title(),
                        default_enabled=value.get("default_enabled", False),
                        config=value,
                        asset=value.get("path"),
                    )
                )
    return result


def default_selection(pack):
    identity = pack["manifest"]["theme"]
    return Selection(
        theme_id=identity["id"], theme_version=identity["version"]
    ).model_dump()


def effective(pack, selection):
    overrides = {v["component_id"]: v for v in selection["overrides"]}
    return [
        {
            **c,
            "enabled": overrides.get(c["id"], {}).get("enabled", c["default_enabled"]),
            "override": overrides.get(c["id"], {}),
        }
        for c in components(pack["manifest"])
    ]


def save_selection(store, pack_id, data):
    pack = store.pack(pack_id)
    selection = Selection.model_validate(data).model_dump()
    identity = pack["manifest"]["theme"]
    if (selection["theme_id"], selection["theme_version"]) != (
        identity["id"],
        identity["version"],
    ):
        raise PackError("Selection belongs to a different theme/version")
    allowed = {c["id"]: c for c in components(pack["manifest"])}
    for override in selection["overrides"]:
        component = allowed.get(override["component_id"])
        if not component:
            raise PackError("Unknown component selection")
        if component.get("required"):
            raise PackError("Use the device target switch for required live wallpapers")
        if override.get("slot_category") and override["slot_category"] not in {
            v["category"] for v in component.get("options", [])
        }:
            raise PackError("Invalid complication category")
    for bindings in selection["device_bindings"].values():
        if not set(bindings) <= set(allowed):
            raise PackError("Unknown device-bound component")
    with LOCK:
        store.put("selection", pack_id, selection)
    return selection


def make_plan(store: Store, pack_id, devices):
    pack = store.pack(pack_id)
    if not pack["complete"]:
        raise PackError(
            "This theme is an incomplete draft: " + "; ".join(pack["issues"])
        )
    selection = store.get("selection", pack_id, default_selection(pack))
    serials = [s for role, s in devices.items() if s and selection["targets"].get(role)]
    if len(serials) != len(set(serials)):
        raise PackError("Phone and watch must use separate ADB devices")
    snapshots = {}
    for role in ("phone", "watch"):
        serial = devices.get(role)
        if serial and selection["targets"][role]:
            try:
                snapshots[role] = device_adapters.discover(serial)
            except adb.AdbError as exc:
                snapshots[role] = {
                    "kind": "unknown",
                    "error": str(exc),
                    "providers": {},
                }
    items = []
    for c in effective(pack, selection):
        role = c["device"]
        if not selection["targets"][role] or not c["enabled"]:
            continue
        serial = devices.get(role)
        snapshot = snapshots.get(role, {})
        reason = "Requires device confirmation."
        mode = "user_action"
        if not serial:
            reason = "Select an authorized " + role + " device."
        elif snapshot.get("error"):
            reason = snapshot["error"]
        elif snapshot.get("kind") != role:
            reason = "Selected device is not a verified " + role
            mode = "unsupported"
        override = c.get("override", {})
        provider_def = c.get("provider")
        category = override.get("slot_category") or c.get("default_category")
        if c["category"] == "complication":
            provider_def = next(
                v["provider"] for v in c["options"] if v["category"] == category
            )
        provider = (
            selection.get("device_bindings", {}).get(serial, {}).get(c["id"])
            or override.get("provider_component")
            or (provider_def or {}).get("component")
        )
        matched = None
        if provider_def and c["category"] != "tile":
            matched = provider_matching.resolve(c, snapshot, selection, serial)
            provider = matched["component"] if matched else None
            if not matched and serial and snapshot.get("kind") == role:
                mode = "unsupported"
                reason = (
                    "Unavailable: no verified compatible provider. Deselect to skip."
                )
        elif c["category"] == "tile" and provider:
            query = snapshot.get("providers", {}).get("wear_os_tile", {})
            if query.get("status") == "queried" and provider not in query["components"]:
                mode = "unsupported"
                reason = (
                    "Tile provider unavailable on the selected watch. Deselect to skip."
                )
        config = c.get("config", {})
        instructions = []
        if c["category"] == "live" and role == "phone":
            instructions = [
                "The MP4 will be copied to Download/ThemeLab on this phone.",
                "Open Wallpaper and style > Change wallpapers > Gallery. Select the transferred video and confirm the supported wallpaper destination.",
                "If your launcher needs a live-wallpaper engine, use an already installed compatible engine; ThemeLab does not install one automatically.",
                "After checking the animation on the phone, confirm completion here.",
            ]
        elif c["category"] == "live":
            reason = "Animated artwork requires a built watch face; copying it does not install or activate a face."
            instructions = [
                "Export this theme pack to your computer. In Watch Face Studio create/open a compatible face; import the animated WebP/GIF as an animation layer.",
                "Add live time and your selected complication slots; use a static/minimal-clock AOD state and run the editor's compatibility/memory checks.",
                "Connect this watch separately using Watch Face Studio's Run on Device workflow, build/deploy the face and select it on the watch.",
                "Use the exported manifest/selection as the layout reference. Confirm here only after the animated face is actually active.",
            ]
        elif "provider" in c and role == "phone":
            instructions = [
                "Touch and hold an empty Home screen area > Widgets.",
                "Choose "
                + (matched["label"] if matched else c["label"])
                + " from an installed provider, place it and configure it.",
                "Suggested size/layout: " + json.dumps(c.get("placement", {})),
                "Android may ask you to tap Add. ThemeLab advances after it detects the new launcher binding.",
            ]
        elif c["category"] == "complication":
            instructions = [
                "Use "
                + (matched["label"] if matched else "a compatible provider")
                + " in the face editor.",
                "Touch and hold the active watch face > Customize/Edit > Complications.",
                "Select slot "
                + str(c["slot_id"])
                + " and choose "
                + str(category).replace("_", " ")
                + " from a compatible available source.",
                "Only use a face with a compatible slot/type. Confirm after the real data appears; otherwise skip or change provider.",
            ]
        elif c["category"] == "tile":
            instructions = [
                "On the watch, swipe to the Tiles carousel and choose Add tiles, or use Galaxy Wearable > Tiles.",
                "Add/reorder "
                + c["label"]
                + " from an installed provider, then confirm here.",
            ]
        else:
            reason = "Provider/device-specific setting; configure using the supported editor or device settings."
            instructions = [
                "Requested configuration: " + json.dumps(config),
                "Use the supported launcher/device settings or Watch Face Studio; no universal automatic API is available in this installation.",
                "Confirm only after checking the result on the device, or skip.",
            ]
        items.append(
            ApplyItem(
                id=c["id"],
                device=role,
                category=c["category"],
                label=c["label"],
                serial=serial,
                provider=provider,
                provider_label=matched["label"] if matched else None,
                asset=c.get("asset"),
                mode=mode,
                reason=reason,
                instructions=instructions,
            ).model_dump()
        )
    plan = dict(
        id=uuid.uuid4().hex,
        pack_id=pack_id,
        created_at=now(),
        selection=selection,
        devices=devices,
        capabilities=snapshots,
        items=items,
    )
    store.put("plan", plan["id"], plan)
    return plan


def summarize(job):
    statuses = [i["status"] for i in job["items"]]
    if job.get("running"):
        return "running"
    if all(s == "applied" for s in statuses) and statuses:
        return "completed"
    if "pending_user_action" in statuses:
        return (
            "partial"
            if any(s in ("failed", "unsupported", "skipped") for s in statuses)
            else "pending_user_action"
        )
    if any(s == "applied" for s in statuses):
        return "partial"
    if statuses and all(s == "skipped" for s in statuses):
        return "cancelled"
    return "failed"


def _persist(store, job):
    job["updated_at"] = now()
    job["status"] = summarize(job)
    store.put("job", job["id"], job)


def run_job(store, job_id):
    try:
        with LOCK:
            job = store.get("job", job_id)
            job["running"] = True
            _persist(store, job)
        for item_id in [i["id"] for i in job["items"]]:
            with LOCK:
                job = store.get("job", job_id)
                item = next(i for i in job["items"] if i["id"] == item_id)
                if item["status"] is not None:
                    continue
                if job.get("cancel_requested"):
                    item.update(status="skipped", reason="Cancelled before this action")
                    _persist(store, job)
                    continue
                item["attempts"] += 1
                _persist(store, job)
                item = json.loads(json.dumps(item))
            try:
                if item["mode"] == "unsupported":
                    item["status"] = "unsupported"
                else:
                    if not item["serial"]:
                        raise adb.AdbError("Select a device and create a new plan")
                    current = device_adapters.discover(item["serial"])
                    if current.get("kind") != item["device"]:
                        raise adb.AdbError(
                            "Device role could not be verified; reconnect and review a new plan"
                        )
                    pack = store.pack(job["pack_id"])
                    plan = store.get("plan", job["plan_id"])
                    component = next(
                        c
                        for c in effective(pack, plan["selection"])
                        if c["id"] == item_id
                    )
                    if (
                        component.get("provider") or component.get("options")
                    ) and component["category"] != "tile":
                        verified = provider_matching.resolve(
                            component, current, plan["selection"], item["serial"]
                        )
                        if not verified or verified["component"] != item["provider"]:
                            raise adb.AdbError(
                                "Provider capabilities changed. Review a new plan."
                            )
                    if item["provider"]:
                        platform = (
                            "android_appwidget"
                            if item["device"] == "phone"
                            else (
                                "wear_os_tile"
                                if item["category"] == "tile"
                                else "wear_os_complication"
                            )
                        )
                        query = current.get("providers", {}).get(platform, {})
                        if (
                            query.get("status") == "queried"
                            and item["provider"] not in query["components"]
                        ):
                            raise adb.AdbError(
                                "Provider is no longer available; change provider and review a new plan"
                            )
                    adapter = device_adapters.ADAPTERS[item["device"]]
                    if item["asset"]:
                        source = safe_path(store.assets / job["pack_id"], item["asset"])
                        expected = next(
                            (
                                r.get("sha256")
                                for r in asset_records(pack["manifest"])
                                if r["path"] == item["asset"]
                            ),
                            None,
                        )
                        if (
                            expected
                            and hashlib.sha256(source.read_bytes()).hexdigest()
                            != expected
                        ):
                            raise PackError(
                                "Stored asset checksum changed; reimport a clean pack"
                            )
                        transfer = adapter.stage(
                            item["serial"], source, job["pack_id"], item["id"]
                        )
                        item["staged_files"].append(transfer)
                    if item["category"] == "live":
                        try:
                            adapter.open_settings(item["serial"])
                        except adb.AdbError as exc:
                            item["reason"] += " " + str(exc)
                    if (
                        component.get("provider", {}).get("platform")
                        == "android_appwidget"
                    ):
                        item["launcher"] = current["launcher"]
                        item["prior_widget_ids"] = device_adapters.launcher_widget_ids(
                            item["serial"], current["launcher"], item["provider"]
                        )
                    item["status"] = "pending_user_action"
            except (adb.AdbError, PackError, OSError) as exc:
                item.update(status="failed", reason=str(exc))
            with LOCK:
                job = store.get("job", job_id)
                if (
                    job.get("cancel_requested")
                    and item["status"] == "pending_user_action"
                ):
                    item.update(
                        status="skipped",
                        reason="Cancelled; staged files retained, no user settings reverted",
                    )
                job["items"] = [item if i["id"] == item_id else i for i in job["items"]]
                _persist(store, job)
            if "prior_widget_ids" in item and item["status"] == "pending_user_action":
                break
    except Exception as exc:
        with LOCK:
            job = store.get("job", job_id)
            for item in job["items"]:
                if item["status"] is None:
                    item.update(
                        status="failed",
                        reason="Execution interrupted: " + str(exc)[:200],
                    )
            store.put("job", job_id, job)
    finally:
        with LOCK:
            job = store.get("job", job_id)
            job["running"] = False
            _persist(store, job)


def start_job(store, pack_id, plan_id, submit=True):
    with LOCK:
        plan = store.get("plan", plan_id)
        if not plan or plan["pack_id"] != pack_id:
            raise PackError("Apply plan not found")
        if not plan["items"]:
            raise PackError("No components selected")
        if store.get("plan_job", plan_id):
            raise PackError("This plan already has a job; resume or retry that job")
        if (
            store.get("selection", pack_id, default_selection(store.pack(pack_id)))
            != plan["selection"]
        ):
            raise PackError("Selections changed; review a new plan")
        serials = {i["serial"] for i in plan["items"] if i["serial"]}
        for active in store.all("job"):
            if active.get("running") or any(
                i["status"] == "pending_user_action" for i in active["items"]
            ):
                if serials & {i["serial"] for i in active["items"]}:
                    raise PackError(
                        "A device already has an unfinished apply job; finish or cancel it first"
                    )
        job = dict(
            id=uuid.uuid4().hex,
            pack_id=pack_id,
            plan_id=plan_id,
            items=plan["items"],
            created_at=now(),
            running=True,
            cancel_requested=False,
        )
        _persist(store, job)
        store.put("plan_job", plan_id, job["id"])
    if submit:
        EXECUTOR.submit(run_job, store, job["id"])
    return job


def acknowledge(store, job_id, item_id, outcome, confirmed=False):
    with LOCK:
        job = store.get("job", job_id)
        if not job:
            raise KeyError("Apply job not found")
        if job["running"]:
            raise PackError("Wait for device transfers to finish")
        item = next((i for i in job["items"] if i["id"] == item_id), None)
        if not item or item["status"] != "pending_user_action":
            raise PackError("Only a pending device action can be confirmed or skipped")
        if outcome == "applied" and "prior_widget_ids" in item:
            raise PackError("Waiting for Android to verify the new launcher widget")
        if outcome == "applied" and not confirmed:
            raise PackError("Confirm you checked the result on the device")
        item.update(
            status=outcome,
            verification="user_reported" if outcome == "applied" else "none",
        )
        resume_after_widget(store, job, item)
        return job


def resume_after_widget(store, job, item):
    resume = "prior_widget_ids" in item and any(
        i["status"] is None for i in job["items"]
    )
    job["running"] = resume
    _persist(store, job)
    if resume:
        EXECUTOR.submit(run_job, store, job["id"])


def verify_placement(store, job_id):
    job = store.get("job", job_id)
    if not job:
        raise KeyError("Apply job not found")
    item = next(
        (
            i
            for i in job["items"]
            if "prior_widget_ids" in i and i["status"] == "pending_user_action"
        ),
        None,
    )
    if job["running"] or not item:
        return job
    current = device_adapters.launcher_widget_ids(
        item["serial"], item["launcher"], item["provider"]
    )
    added = set(current) - set(item["prior_widget_ids"])
    if len(added) != 1:
        return job
    with LOCK:
        job = store.get("job", job_id)
        actual = next(i for i in job["items"] if i["id"] == item["id"])
        if (
            actual["status"] == "pending_user_action"
            and not job["running"]
            and not job["cancel_requested"]
        ):
            actual.update(
                status="applied", verification="launcher_binding", widget_id=added.pop()
            )
            resume_after_widget(store, job, actual)
        return job


def cancel_job(store, job_id):
    with LOCK:
        job = store.get("job", job_id)
        if not job:
            raise KeyError("Apply job not found")
        job["cancel_requested"] = True
        for item in job["items"]:
            if item["status"] == "pending_user_action" or (
                not job["running"] and item["status"] is None
            ):
                item.update(
                    status="skipped", reason="Cancelled; existing settings unchanged"
                )
        _persist(store, job)
        return job


def retry_job(store, job_id):
    with LOCK:
        job = store.get("job", job_id)
        if not job:
            raise KeyError("Apply job not found")
        if job["running"]:
            raise PackError("Job is already running")
        if any(
            "prior_widget_ids" in i and i["status"] == "pending_user_action"
            for i in job["items"]
        ):
            raise PackError("Finish or skip the pending widget before retrying")
        if not any(i["status"] == "failed" for i in job["items"]):
            raise PackError("There are no failed items to retry")
        for other in store.all("job"):
            if (
                other["id"] != job_id
                and (
                    other.get("running")
                    or any(i["status"] == "pending_user_action" for i in other["items"])
                )
                and {i["serial"] for i in other["items"]}
                & {i["serial"] for i in job["items"] if i["serial"]}
            ):
                raise PackError("Device has another unfinished job")
        for item in job["items"]:
            if item["status"] == "failed":
                item["status"] = None
        job.update(running=True, cancel_requested=False)
        _persist(store, job)
    EXECUTOR.submit(run_job, store, job_id)
    return job


def recover_jobs(store):
    with LOCK:
        for job in store.all("job"):
            if job.get("running"):
                for item in job["items"]:
                    if item["status"] is None:
                        item.update(
                            status="failed",
                            reason="Backend stopped during execution; check device before retrying",
                        )
                job.update(running=False)
                _persist(store, job)


def cleanup_staging(store, job_id):
    """Best-effort undo of owned staging only, never active wallpaper/settings."""
    with LOCK:
        job = store.get("job", job_id)
        if not job:
            raise KeyError("Apply job not found")
        if job["running"] or any(
            i["status"] in ("applied", "pending_user_action") for i in job["items"]
        ):
            raise PackError(
                "Cancel pending actions first. Staging for a reported applied theme cannot be removed safely."
            )
        other_paths = {
            (f.get("serial", i["serial"]), f["path"])
            for other in store.all("job")
            if other["id"] != job_id
            for i in other["items"]
            for f in i["staged_files"]
            if not f.get("removed")
        }
        for item in job["items"]:
            for file in item["staged_files"]:
                if file.get("removed") or not file.get("created_by_job"):
                    continue
                if not file.get("serial") or file["serial"] != item["serial"]:
                    file["cleanup_error"] = (
                        "Device ownership could not be verified; retained"
                    )
                    continue
                if (file["serial"], file["path"]) in other_paths:
                    file["cleanup_error"] = "Another job references this staged file"
                    continue
                pattern = (
                    r"/sdcard/Download/ThemeLab/"
                    + re.escape(job["pack_id"])
                    + r"/[a-z0-9_-]+\.[a-z0-9]{1,8}"
                )
                if (
                    not file.get("transfer_verified")
                    or not file.get("sha256")
                    or not re.fullmatch(pattern, file["path"])
                ):
                    file["cleanup_error"] = (
                        "Ownership/content could not be verified; retained"
                    )
                    continue
                try:
                    observed = str(
                        adb.on_device(
                            file["serial"], "shell", "sha256sum", file["path"]
                        )
                    ).split()[0]
                    if observed != file["sha256"]:
                        raise adb.AdbError("Staged content changed; retained")
                    adb.on_device(file["serial"], "shell", "rm", "--", file["path"])
                    file["removed"] = True
                    file.pop("cleanup_error", None)
                except (adb.AdbError, IndexError) as exc:
                    file["cleanup_error"] = str(exc)
        _persist(store, job)
        return job
