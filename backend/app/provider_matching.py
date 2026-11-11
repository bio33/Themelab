"""Resolve only capabilities attributable to a particular installed component."""

import re

# AOSP Calendar's manifest declares this receiver, its widget displays upcoming events.
# https://android.googlesource.com/platform/packages/apps/Calendar/+/refs/heads/main/
AUDITED = {
    (
        "android_appwidget",
        "com.android.calendar/com.android.calendar.widget.CalendarAppWidgetProvider",
    ): ("agenda", "Calendar agenda"),
}
NATIVE_CATEGORIES = {"clock", "analog_clock", "digital_clock", "weather", "moon_phase"}
TYPE_KEY = "android.support.wearable.complications.SUPPORTED_TYPES"


def canonical(component):
    package, _, name = component.partition("/")
    return package + "/" + (package + name if name.startswith(".") else name)


def resolve(component, snapshot, selection, serial):
    override = component.get("override", {})
    category = override.get("slot_category") or component.get("default_category")
    provider = component.get("provider")
    if component.get("category") == "complication":
        provider = next(
            (v["provider"] for v in component["options"] if v["category"] == category),
            None,
        )
    if not provider or snapshot.get("kind") != component["device"]:
        return None
    platform = provider["platform"]
    category = category or component["category"]
    query = snapshot.get("providers", {}).get(platform, {})
    if query.get("status") != "queried":
        return None
    preferred = selection.get("device_bindings", {}).get(serial, {}).get(
        component["id"]
    ) or override.get("provider_component")
    candidates = []
    for record in query.get("capabilities", []):
        if (
            record["component"] not in query["components"]
            or record["category"] != category
        ):
            continue
        if (
            platform == "android_appwidget"
            and category in NATIVE_CATEGORIES
            and not record.get("native")
        ):
            continue
        if platform == "wear_os_complication" and not (
            set(record["types"])
            & (
                set(component["supported_types"])
                - {"EMPTY", "NO_DATA", "NOT_CONFIGURED"}
            )
        ):
            continue
        candidates.append(record)
    return min(
        candidates,
        key=lambda v: (v["component"] != preferred, v["label"], v["component"]),
        default=None,
    )


def from_manifest(root, platform, installed):
    """Read the selected APK's receiver/service declaration, scoped to that component."""
    from .apk_manifest import ANDROID

    pkg = root.get("package", "")
    installed_names = {canonical(name): name for name in installed}
    tag = "receiver" if platform == "android_appwidget" else "service"
    result = []
    for node in root.findall("application/" + tag):
        name = node.get(ANDROID + "name", "")
        if "." not in name:
            name = "." + name
        identity = canonical(pkg + "/" + name)
        if identity not in installed_names or node.get(ANDROID + "enabled") == "false":
            continue
        fields = {
            child.get(ANDROID + "name"): child.get(ANDROID + "value", "")
            for child in node.findall("meta-data")
        }
        category, label = AUDITED.get((platform, identity), ("", ""))
        if not category:
            category = fields.get("themelab.provider.category", "")
            label = fields.get("themelab.provider.label") or node.get(
                ANDROID + "label", ""
            )
        if (
            not re.fullmatch(r"[a-z][a-z_]{0,63}", category)
            or not label.strip()
            or label.startswith("@")
        ):
            continue
        if platform == "android_appwidget":
            if not any(
                c.get(ANDROID + "name") == "android.appwidget.provider"
                and c.get(ANDROID + "resource", "").startswith("@")
                for c in node.findall("meta-data")
            ):
                continue
        elif (
            node.get(ANDROID + "permission")
            != "com.google.android.wearable.permission.BIND_COMPLICATION_PROVIDER"
            or node.get(ANDROID + "exported") == "false"
        ):
            continue
        result.append(
            {
                "component": installed_names[identity],
                "category": category,
                "label": label.strip()[:100],
                "types": fields.get(TYPE_KEY, "").replace(" ", "").split(","),
            }
        )
    return result
