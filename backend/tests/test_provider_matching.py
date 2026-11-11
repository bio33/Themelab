from app import device_adapters
from app import provider_matching as matching


def test_slot_requires_semantics_and_usable_type_even_with_saved_binding():
    slot = {
        "id": "slot",
        "device": "watch",
        "category": "complication",
        "default_category": "date",
        "supported_types": ["SHORT_TEXT", "EMPTY"],
        "options": [
            {"category": "date", "provider": {"platform": "wear_os_complication"}}
        ],
    }
    wrong = {
        "component": "example/.Old",
        "category": "date",
        "types": ["EMPTY", "LONG_TEXT"],
        "label": "Old source",
    }
    right = {
        "component": "example/.Date",
        "category": "date",
        "types": ["SHORT_TEXT"],
        "label": "Today's date",
    }
    battery = {
        "component": "example/.Battery",
        "category": "battery",
        "types": ["SHORT_TEXT"],
        "label": "Battery",
    }
    query = {
        "status": "queried",
        "components": [r["component"] for r in [right, wrong, battery]],
        "capabilities": [wrong, battery, right],
    }
    snap = {"kind": "watch", "providers": {"wear_os_complication": query}}
    selection = {"device_bindings": {"WATCH": {"slot": wrong["component"]}}}
    assert matching.resolve(slot, snap, selection, "WATCH") == right
    query["components"].remove(right["component"])
    assert matching.resolve(slot, snap, selection, "WATCH") is None


def test_clock_ignores_game_even_when_installed_and_saved():
    widget = {
        "id": "clock",
        "device": "phone",
        "category": "digital_clock",
        "provider": {"platform": "android_appwidget"},
    }
    game = {"component": "browser/.Dino", "category": "game", "label": "Dinosaur"}
    clock = {
        "component": "studio/.Digital",
        "category": "digital_clock",
        "label": "ThemeLab clock",
        "native": True,
    }
    query = {
        "status": "queried",
        "components": [r["component"] for r in (game, clock)],
        "capabilities": [game, clock],
    }
    snap = {"kind": "phone", "providers": {"android_appwidget": query}}
    saved = {"device_bindings": {"PHONE": {"clock": game["component"]}}}
    assert matching.resolve(widget, snap, saved, "PHONE") == clock
    query["capabilities"] = [game]
    assert matching.resolve(widget, snap, saved, "PHONE") is None


def test_manifest_metadata_is_scoped_to_service_and_installed_component():
    from xml.etree.ElementTree import fromstring

    xml = """<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="sample">
      <application><meta-data android:name="themelab.provider.category" android:value="battery"/>
        <service android:name=".Date" android:permission="com.google.android.wearable.permission.BIND_COMPLICATION_PROVIDER">
          <meta-data android:name="themelab.provider.category" android:value="date"/>
          <meta-data android:name="themelab.provider.label" android:value="Calendar date"/>
          <meta-data android:name="android.support.wearable.complications.SUPPORTED_TYPES" android:value="SHORT_TEXT,LONG_TEXT"/>
        </service></application></manifest>"""
    root = fromstring(xml)
    records = matching.from_manifest(root, "wear_os_complication", ["sample/.Date"])
    assert records == [
        {
            "component": "sample/.Date",
            "category": "date",
            "label": "Calendar date",
            "types": ["SHORT_TEXT", "LONG_TEXT"],
        }
    ]
    assert matching.from_manifest(root, "wear_os_complication", ["sample/.Other"]) == []


def test_launcher_binding_requires_matching_host_user_and_provider(monkeypatch):
    dump = """Widgets:
  [0] id=14
    host=HostId{user:0, app:1222, hostId:4, pkg:home}
    provider=ProviderId{user:0, app:990, cmp:ComponentInfo{calendar/calendar.Agenda}}
  [1] id=15
    host=HostId{user:10, app:1222, hostId:4, pkg:home}
    provider=ProviderId{user:10, app:990, cmp:ComponentInfo{calendar/calendar.Agenda}}
Hosts:
"""
    monkeypatch.setattr(
        device_adapters.adb,
        "on_device",
        lambda serial, *args: "0" if "am" in args else dump,
    )
    assert device_adapters.launcher_widget_ids(
        "PHONE", "home/.Main", "calendar/.Agenda"
    ) == [14]
    assert (
        device_adapters.launcher_widget_ids("PHONE", "other/.Main", "calendar/.Agenda")
        == []
    )


def test_device_discovery_reads_compiled_manifest_and_rejects_unlisted_components(
    monkeypatch,
):
    import struct

    from app import apk_manifest

    # Android ResXMLTree UTF-8 string pool and typed element attributes.
    strings = []
    chunks = []

    def index(value):
        if value not in strings:
            strings.append(value)
        return strings.index(value)

    def element(name, attrs=(), children=None):
        null = apk_manifest.NONE
        attr_bytes = b""
        for key, value in attrs:
            namespace = null if key == "package" else index(apk_manifest.ANDROID[1:-1])
            attr_bytes += struct.pack(
                "<IIIHBBI", namespace, index(key), null, 8, 0, 3, index(value)
            )
        tag = index(name)
        ext = struct.pack("<II6H", null, tag, 20, 20, len(attrs), 0, 0, 0)
        chunks.append(
            struct.pack("<HHIII", 0x102, 16, 16 + len(ext + attr_bytes), 1, null)
            + ext
            + attr_bytes
        )
        if children:
            children()
        chunks.append(struct.pack("<HHIIIII", 0x103, 16, 24, 1, null, null, tag))

    def service():
        element(
            "service",
            [
                ("name", ".Date"),
                (
                    "permission",
                    "com.google.android.wearable.permission.BIND_COMPLICATION_PROVIDER",
                ),
            ],
            lambda: [
                element("meta-data", [("name", key), ("value", value)])
                for key, value in [
                    ("themelab.provider.category", "date"),
                    ("themelab.provider.label", "Today's date"),
                    (matching.TYPE_KEY, "SHORT_TEXT"),
                ]
            ],
        )

    element(
        "manifest",
        [("package", "sample")],
        lambda: element("application", children=service),
    )
    pool = b""
    offsets = []
    for value in strings:
        encoded = value.encode()
        offsets.append(len(pool))
        pool += bytes([len(value), len(encoded)]) + encoded + bytes(1)
    pool += bytes(-len(pool) % 4)
    first = 28 + 4 * len(strings)
    pool_header = struct.pack(
        "<HH6I", 1, 28, first + len(pool), len(strings), 0, 0x100, first, 0
    )
    payload = (
        pool_header
        + struct.pack(f"<{len(strings)}I", *offsets)
        + pool
        + b"".join(chunks)
    )
    manifest = struct.pack("<HHI", 3, 8, 8 + len(payload)) + payload

    def query(serial, *args, **kwargs):
        assert serial == "WATCH"
        return (
            manifest
            if kwargs.get("binary")
            else "package:/data/app/test/base.apk=sample"
        )

    monkeypatch.setattr(device_adapters.adb, "on_device", query)
    records = device_adapters.installed_capabilities(
        "WATCH", "wear_os_complication", ["sample/.Date"]
    )
    assert records == [
        {
            "component": "sample/.Date",
            "category": "date",
            "label": "Today's date",
            "types": ["SHORT_TEXT"],
        }
    ]
    assert (
        device_adapters.installed_capabilities(
            "WATCH", "wear_os_complication", ["sample/.Battery"]
        )
        == []
    )
