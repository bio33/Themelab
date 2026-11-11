import hashlib
import io
import json
import stat
import subprocess
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import adb, main, pack_service as service, device_adapters
from app.pack_import import PackError, import_archive
from app.pack_store import Store


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp("media")
    video = root / "phone.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x96:rate=10",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "2",
            str(video),
        ],
        check=True,
    )
    frames = [Image.new("RGB", (64, 64), color) for color in ("red", "blue", "green")]
    frames[0].save(
        root / "watch.webp",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
        lossless=True,
    )
    frames[0].save(root / "still.png")
    return {p.name: p.read_bytes() for p in root.iterdir()}


def asset(media, name, mime, width, height, live=False):
    result = dict(
        path=name,
        mime_type=mime,
        sha256=hashlib.sha256(media[name]).hexdigest(),
        width=width,
        height=height,
    )
    if live:
        result.update(
            duration_seconds=1 if mime == "video/mp4" else 0.3,
            fps=10,
            loop=True,
            audio=False,
        )
    return result


def manifest(media):
    return dict(
        schema_version="1.0.0",
        theme=dict(
            id="test-theme",
            title="Test Theme",
            version="1.0.0",
            palette=dict(background="#000000", foreground="#FFFFFF", accent="#FFCC00"),
        ),
        generation_policy=dict(
            phone_live_required=True,
            watch_live_required=True,
            fetch_missing_optional_components=False,
        ),
        phone=dict(
            live_wallpaper=asset(media, "phone.mp4", "video/mp4", 64, 96, True),
            widgets=[
                dict(
                    id="clock-widget",
                    label="Clock",
                    category="analog_clock",
                    default_enabled=True,
                    availability="suggested",
                    apply_mode="user_action",
                    provider=dict(
                        platform="android_appwidget",
                        category="clock",
                        strategy="discover_on_device",
                        component=None,
                    ),
                )
            ],
        ),
        watch=dict(
            live_background=asset(media, "watch.webp", "image/webp", 64, 64, True)
        ),
    )


def archive(data, media, extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("manifest.json", json.dumps(data))
        for name, raw in media.items():
            z.writestr(name, raw)
        for name, raw in (extra or {}).items():
            z.writestr(name, raw)
    return buffer.getvalue()


@pytest.fixture
def imported(tmp_path, media):
    store = Store(tmp_path / "packs")
    pack = import_archive(store, archive(manifest(media), media))[0]
    return store, pack


def test_real_media_import_reimport_export_and_range(imported, media, monkeypatch):
    store, pack = imported
    assert pack["complete"] and pack["source"] == "standard"
    assert import_archive(store, archive(manifest(media), media))[0]["id"] == pack["id"]
    monkeypatch.setattr(main, "PACKS_ROOT", store.root)
    client = TestClient(main.app)
    detail = client.get("/api/packs/" + pack["id"]).json()
    assert detail["selection"]["targets"] == {"phone": True, "watch": True}
    response = client.get(
        "/api/packs/" + pack["id"] + "/assets/phone.mp4",
        headers={"Range": "bytes=0-99"},
    )
    assert response.status_code == 206 and len(response.content) == 100
    exported = client.get("/api/packs/" + pack["id"] + "/export")
    assert exported.status_code == 200
    fresh = Store(store.root / "fresh")
    assert import_archive(fresh, exported.content)[0]["complete"]
    assert (
        client.get("/api/packs/" + pack["id"] + "/assets/manifest.json").status_code
        == 404
    )


@pytest.mark.parametrize(
    "device,field", [("phone", "live_wallpaper"), ("watch", "live_background")]
)
def test_required_animation_cannot_be_omitted(tmp_path, media, device, field):
    data = manifest(media)
    data[device].pop(field)
    with pytest.raises(PackError, match="required"):
        import_archive(Store(tmp_path), archive(data, media))


def test_all_optional_components_can_be_omitted(tmp_path, media):
    data = manifest(media)
    data["phone"].pop("widgets")
    pack = import_archive(Store(tmp_path), archive(data, media))[0]
    assert pack["complete"]
    assert len(service.components(pack["manifest"])) == 2


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/escape",
        "C:/escape",
        "dir/../../escape",
        "dir\\escape",
        "./escape",
    ],
)
def test_zip_traversal_rejected(tmp_path, media, name):
    store = Store(tmp_path)
    with pytest.raises(PackError):
        import_archive(store, archive(manifest(media), media, {name: b"bad"}))
    assert not store.packs()


def test_symlink_and_duplicate_paths_rejected(tmp_path, media):
    for kind in ("link", "duplicate"):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as z:
            z.writestr("manifest.json", json.dumps(manifest(media)))
            if kind == "link":
                link = zipfile.ZipInfo("phone.mp4")
                link.create_system = 3
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(link, "/etc/passwd")
            else:
                z.writestr("MANIFEST.JSON", "{}")
        with pytest.raises(PackError):
            import_archive(Store(tmp_path / kind), raw.getvalue())


def test_compression_bomb_and_count_limit(tmp_path, media, monkeypatch):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bomb", b"0" * 100_000)
    with pytest.raises(PackError, match="compressed"):
        import_archive(Store(tmp_path / "bomb"), raw.getvalue())
    monkeypatch.setattr("app.pack_import.MAX_ENTRIES", 2)
    with pytest.raises(PackError, match="entries"):
        import_archive(Store(tmp_path / "count"), archive(manifest(media), media))


@pytest.mark.parametrize(
    "change,pattern",
    [
        ("version", "Unsupported schema"),
        ("hash", "Checksum"),
        ("dimensions", "width"),
        ("watch-still", "infinitely looping"),
        ("mime", "content"),
        ("duplicate-id", "unique"),
    ],
)
def test_invalid_metadata_and_media(tmp_path, media, change, pattern):
    data = manifest(media)
    if change == "version":
        data["schema_version"] = "2.0.0"
    if change == "hash":
        data["phone"]["live_wallpaper"]["sha256"] = "0" * 64
    if change == "dimensions":
        data["phone"]["live_wallpaper"]["width"] = 999
    if change == "watch-still":
        data["watch"]["live_background"] = asset(
            media, "still.png", "image/webp", 64, 64, True
        )
    if change == "mime":
        data["watch"]["live_background"] = asset(
            media, "watch.webp", "image/gif", 64, 64, True
        )
    if change == "duplicate-id":
        data["phone"]["widgets"] *= 2
    with pytest.raises(PackError, match=pattern):
        import_archive(Store(tmp_path), archive(data, media))


def test_phone_audio_and_static_video_rejected(tmp_path, media):
    for kind in ("audio", "static"):
        path = tmp_path / (kind + ".mp4")
        argv = [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x96:r=10",
        ]
        if kind == "audio":
            argv += ["-f", "lavfi", "-i", "sine=frequency=440"]
        subprocess.run(
            argv + ["-t", "1", "-c:v", "libx264", "-threads", "2", str(path)],
            check=True,
        )
        changed = {**media, "phone.mp4": path.read_bytes()}
        with pytest.raises(PackError, match="no audio|appears static"):
            import_archive(Store(tmp_path / kind), archive(manifest(changed), changed))


def test_collection_atomicity_and_version_conflict(imported, media, tmp_path):
    store, pack = imported
    changed = manifest(media)
    changed["theme"]["title"] = "Different"
    with pytest.raises(PackError, match="already exists"):
        import_archive(store, archive(changed, media))
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        for n in (1, 2):
            data = manifest(media)
            data["theme"]["id"] = "theme-" + str(n)
            if n == 2:
                data["watch"]["live_background"]["sha256"] = "0" * 64
            z.writestr(f"{n}/manifest.json", json.dumps(data))
            for name, value in media.items():
                z.writestr(f"{n}/{name}", value)
    with pytest.raises(PackError, match="Checksum"):
        import_archive(store, raw.getvalue())
    assert len(store.packs()) == 1
    assert len(list(store.assets.iterdir())) == 1


def test_legacy_migration_preserves_stills_and_reports_draft(tmp_path, media):
    data = dict(
        id="123456abcdef", name="Old Theme", wallpaper={"filename": "still.png"}
    )
    store = Store(tmp_path)
    pack = import_archive(store, archive(data, {"still.png": media["still.png"]}))[0]
    assert not pack["complete"] and len(pack["issues"]) == 2
    assert (store.assets / pack["id"] / "still.png").read_bytes() == media["still.png"]
    with pytest.raises(PackError, match="incomplete draft"):
        service.make_plan(store, pack["id"], {})


def test_selection_precedence_persistence_and_device_binding(imported):
    store, pack = imported
    selected = service.default_selection(pack)
    selected["overrides"] = [{"component_id": "clock-widget", "enabled": False}]
    selected["device_bindings"] = {"PHONE": {"clock-widget": "org.example/.Clock"}}
    service.save_selection(store, pack["id"], selected)
    restored = Store(store.root).get("selection", pack["id"])
    assert not next(
        c for c in service.effective(pack, restored) if c["id"] == "clock-widget"
    )["enabled"]
    assert restored["device_bindings"]["PHONE"]["clock-widget"] == "org.example/.Clock"
    assert store.pack(pack["id"])["manifest"] == pack["manifest"]
    selected["overrides"] = [{"component_id": "phone-live", "enabled": False}]
    with pytest.raises(PackError, match="target switch"):
        service.save_selection(store, pack["id"], selected)


@pytest.fixture
def adapters(monkeypatch):
    calls = []

    def discover(serial):
        if serial == "OFFLINE":
            raise adb.AdbError("Disconnected")
        return dict(
            kind="phone" if serial == "PHONE" else "watch",
            providers={
                "android_appwidget": dict(
                    status="queried", components=["org.example/.Clock"]
                ),
                "wear_os_complication": dict(status="unknown", components=[]),
            },
        )

    def stage(serial, source, pack_id, label):
        calls.append((serial, label))
        return dict(
            serial=serial,
            path="/sdcard/Download/ThemeLab/" + pack_id + "/" + label,
            created_by_job=True,
            transfer_verified=True,
        )

    adapter = SimpleNamespace(stage=stage, open_settings=lambda serial: None)
    monkeypatch.setattr(device_adapters, "discover", discover)
    monkeypatch.setattr(
        device_adapters, "ADAPTERS", {"phone": adapter, "watch": adapter}
    )
    return calls


def test_plan_only_selected_targets_and_provider_unavailability(imported, adapters):
    store, pack = imported
    selected = service.default_selection(pack)
    selected["targets"]["watch"] = False
    selected["overrides"] = [dict(component_id="clock-widget", enabled=False)]
    service.save_selection(store, pack["id"], selected)
    plan = service.make_plan(store, pack["id"], {"phone": "PHONE", "watch": "WATCH"})
    assert [i["id"] for i in plan["items"]] == ["phone-live"]
    selected["overrides"] = [
        dict(
            component_id="clock-widget",
            enabled=True,
            provider_component="org.missing/.Clock",
        )
    ]
    service.save_selection(store, pack["id"], selected)
    plan = service.make_plan(store, pack["id"], {"phone": "PHONE"})
    assert plan["items"][1]["mode"] == "unsupported"
    assert store.pack(pack["id"])["complete"]


def test_requested_actions_are_pending_and_user_reported_completion(imported, adapters):
    store, pack = imported
    selection = service.default_selection(pack)
    selection["overrides"] = [dict(component_id="clock-widget", enabled=False)]
    service.save_selection(store, pack["id"], selection)
    plan = service.make_plan(store, pack["id"], {"phone": "PHONE", "watch": "WATCH"})
    job = service.start_job(store, pack["id"], plan["id"], submit=False)
    service.run_job(store, job["id"])
    job = store.get("job", job["id"])
    assert job["status"] == "pending_user_action"
    assert all(i["verification"] == "none" for i in job["items"])
    assert set(adapters) == {("PHONE", "phone-live"), ("WATCH", "watch-live")}
    with pytest.raises(PackError, match="Confirm"):
        service.acknowledge(store, job["id"], "phone-live", "applied")
    for item in job["items"]:
        service.acknowledge(store, job["id"], item["id"], "applied", True)
    final = Store(store.root).get("job", job["id"])
    assert final["status"] == "completed"
    assert all(i["verification"] == "user_reported" for i in final["items"])
    with pytest.raises(PackError, match="already has"):
        service.start_job(store, pack["id"], plan["id"], submit=False)


def test_disconnect_partial_retry_preserves_success(imported, adapters, monkeypatch):
    store, pack = imported
    plan = service.make_plan(store, pack["id"], {"phone": "PHONE", "watch": "OFFLINE"})
    job = service.start_job(store, pack["id"], plan["id"], submit=False)
    service.run_job(store, job["id"])
    service.acknowledge(store, job["id"], "phone-live", "applied", True)
    job = store.get("job", job["id"])
    assert job["status"] == "partial" and job["items"][1]["status"] == "failed"
    monkeypatch.setattr(
        service, "EXECUTOR", SimpleNamespace(submit=lambda fn, *args: fn(*args))
    )
    original = device_adapters.discover
    monkeypatch.setattr(
        device_adapters,
        "discover",
        lambda serial: (
            dict(kind="watch", providers={})
            if serial == "OFFLINE"
            else original(serial)
        ),
    )
    service.retry_job(store, job["id"])
    retried = store.get("job", job["id"])
    assert next(i for i in retried["items"] if i["id"] == "phone-live")["attempts"] == 1
    assert next(i for i in retried["items"] if i["id"] == "watch-live")["attempts"] == 2
    assert adapters.count(("PHONE", "phone-live")) == 1


def test_cancel_role_mismatch_and_recovery(imported, adapters):
    store, pack = imported
    plan = service.make_plan(store, pack["id"], {"phone": "WATCH", "watch": "PHONE"})
    assert all(i["mode"] == "unsupported" for i in plan["items"])
    plan = service.make_plan(store, pack["id"], {"phone": "PHONE", "watch": "WATCH"})
    job = service.start_job(store, pack["id"], plan["id"], submit=False)
    service.cancel_job(store, job["id"])
    service.run_job(store, job["id"])
    assert store.get("job", job["id"])["status"] == "cancelled" and not adapters
    other = service.start_job(
        store,
        pack["id"],
        service.make_plan(store, pack["id"], {"phone": "PHONE", "watch": "WATCH"})[
            "id"
        ],
        submit=False,
    )
    service.recover_jobs(store)
    assert all(i["status"] == "failed" for i in store.get("job", other["id"])["items"])


def test_discovery_uses_real_queries_and_never_installs(monkeypatch):
    commands = []
    monkeypatch.setattr(
        adb,
        "info",
        lambda s: {"serial": s, "model": "Watch", "manufacturer": "samsung"},
    )

    def command(serial, *args, **kwargs):
        commands.append((serial, args))
        if args[:3] == ("shell", "pm", "list"):
            return "feature:android.hardware.type.watch"
        if "query-services" in args:
            return "org.example/.Source"
        return "org.launcher/.Home"

    monkeypatch.setattr(adb, "on_device", command)
    found = device_adapters.discover("WATCH")
    assert found["kind"] == "watch" and found["providers"]["wear_os_complication"][
        "components"
    ] == ["org.example/.Source"]
    assert all(s == "WATCH" for s, _ in commands)
    assert not any("install" in args or "grant" in args for _, args in commands)


def test_staging_does_not_overwrite_unknown_or_changed_file(tmp_path, monkeypatch):
    source = tmp_path / "live.mp4"
    source.write_bytes(b"asset")
    calls = []

    def command(serial, *args, **kwargs):
        calls.append(args)
        if "sha256sum" in args:
            return "0" * 64 + " file"
        return "existing"

    monkeypatch.setattr(adb, "on_device", command)
    with pytest.raises(adb.AdbError, match="refusing overwrite"):
        device_adapters.AndroidAdapter().stage("PHONE", source, "a" * 32, "phone-live")
    assert not any(args[0] == "push" for args in calls)


def test_cleanup_only_deletes_verified_owned_unused_files(
    imported, adapters, monkeypatch
):
    store, pack = imported
    plan = service.make_plan(store, pack["id"], {"phone": "PHONE", "watch": "WATCH"})
    job = service.start_job(store, pack["id"], plan["id"], submit=False)
    service.run_job(store, job["id"])
    with pytest.raises(PackError, match="Cancel"):
        service.cleanup_staging(store, job["id"])
    service.cancel_job(store, job["id"])
    job = store.get("job", job["id"])
    owned = dict(
        serial="PHONE",
        path="/sdcard/Download/ThemeLab/" + pack["id"] + "/phone-live.mp4",
        sha256="a" * 64,
        transfer_verified=True,
        created_by_job=True,
    )
    job["items"][0]["staged_files"] = [owned]
    store.put("job", job["id"], job)
    calls = []

    def command(serial, *args, **kwargs):
        calls.append(args)
        return "b" * 64 + " file"

    monkeypatch.setattr(adb, "on_device", command)
    retained = service.cleanup_staging(store, job["id"])
    assert "changed" in retained["items"][0]["staged_files"][0]["cleanup_error"]
    assert not any("rm" in args for args in calls)
    monkeypatch.setattr(
        adb, "on_device", lambda s, *a, **kw: calls.append(a) or "a" * 64 + " file"
    )
    removed = service.cleanup_staging(store, job["id"])
    assert removed["items"][0]["staged_files"][0]["removed"]
    assert any("rm" in args for args in calls)


def test_api_import_selection_plan_and_job_completion(
    tmp_path, media, adapters, monkeypatch
):
    monkeypatch.setattr(main, "PACKS_ROOT", tmp_path / "packs")
    monkeypatch.setattr(
        service, "EXECUTOR", SimpleNamespace(submit=lambda fn, *args: fn(*args))
    )
    with TestClient(main.app) as client:
        response = client.post(
            "/api/packs/import",
            files={
                "file": (
                    "theme.zip",
                    archive(manifest(media), media),
                    "application/zip",
                )
            },
        )
        assert response.status_code == 201
        pack = response.json()["packs"][0]
        key = pack["id"]
        selected = client.get("/api/packs/" + key + "/selection").json()
        selected["targets"]["watch"] = False
        selected["overrides"] = [dict(component_id="clock-widget", enabled=False)]
        assert (
            client.post("/api/packs/" + key + "/selection", json=selected).status_code
            == 200
        )
        plan = client.post(
            "/api/packs/" + key + "/plan", json={"devices": {"phone": "PHONE"}}
        ).json()
        assert len(plan["items"]) == 1
        job = client.post(
            "/api/packs/" + key + "/jobs", json={"plan_id": plan["id"]}
        ).json()
        current = client.get("/api/packs/jobs/" + job["id"]).json()
        assert current["status"] == "pending_user_action"
        done = client.post(
            "/api/packs/jobs/" + job["id"] + "/items/phone-live/confirm",
            json={"outcome": "applied", "confirmed_on_device": True},
        )
        assert done.json()["status"] == "completed"
        assert done.json()["items"][0]["verification"] == "user_reported"
        reset = client.post("/api/packs/" + key + "/selection/reset").json()
        assert reset["targets"]["watch"] and reset["overrides"] == []


def test_widget_waits_for_single_new_binding_then_resumes(
    imported, adapters, monkeypatch
):
    store, pack = imported
    record = dict(
        component="org.example/.Clock",
        category="analog_clock",
        native=True,
        label="ThemeLab Clock",
    )
    discover = device_adapters.discover

    def with_clock(serial):
        snapshot = discover(serial)
        snapshot["launcher"] = "home/.Main"
        snapshot["providers"]["android_appwidget"]["capabilities"] = [record]
        return snapshot

    monkeypatch.setattr(device_adapters, "discover", with_clock)
    ids = [4]
    monkeypatch.setattr(device_adapters, "launcher_widget_ids", lambda *args: list(ids))
    plan = service.make_plan(store, pack["id"], {"phone": "PHONE", "watch": "WATCH"})
    # Run the widget first so the next real item must wait for Android.
    plan["items"].sort(key=lambda i: i["id"] != "clock-widget")
    store.put("plan", plan["id"], plan)
    job_id = service.start_job(store, pack["id"], plan["id"], submit=False)["id"]
    service.run_job(store, job_id)
    assert (
        service.verify_placement(store, job_id)["items"][0]["status"]
        == "pending_user_action"
    )
    assert not adapters
    with pytest.raises(PackError, match="Waiting for Android"):
        service.acknowledge(store, job_id, "clock-widget", "applied", True)
    ids.extend([5, 6])
    assert (
        service.verify_placement(store, job_id)["items"][0]["status"]
        == "pending_user_action"
    )
    ids.remove(6)
    monkeypatch.setattr(service.EXECUTOR, "submit", lambda fn, *args: fn(*args))
    service.verify_placement(store, job_id)
    job = store.get("job", job_id)
    assert job["items"][0]["widget_id"] == 5
    assert job["items"][0]["verification"] == "launcher_binding"
    assert len(adapters) == 2
    service.verify_placement(store, job_id)
    assert len(adapters) == 2
