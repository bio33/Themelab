"""Typed wire models. The checked-in JSON Schema is the manifest authority."""

from __future__ import annotations

import re
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Asset(Model):
    path: str
    mime_type: str
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    fps: float | None = None
    loop: bool | None = None
    audio: bool | None = None


class Provider(Model):
    platform: Literal["android_appwidget", "wear_os_complication", "wear_os_tile"]
    category: str
    strategy: Literal["discover_on_device"]
    component: str | None = None


class Component(Model):
    id: str
    label: str
    category: str
    default_enabled: bool
    availability: Literal["suggested", "bundled", "external_reference"]
    apply_mode: Literal[
        "automatic_if_supported", "user_action", "provider_specific", "manual"
    ]
    provider: Provider
    style: dict[str, Any] = Field(default_factory=dict)
    placement: dict[str, Any] = Field(default_factory=dict)
    dependency_ids: list[str] = Field(default_factory=list)
    preset_asset: Asset | None = None


class SlotOption(Model):
    category: str
    label: str
    provider: Provider


class Bounds(Model):
    x: float
    y: float
    width: float
    height: float


class Complication(Model):
    id: str
    slot_id: int
    bounds: Bounds
    supported_types: list[str]
    default_enabled: bool
    default_category: str
    options: list[SlotOption]
    style: dict[str, Any]
    apply_mode: Literal["user_action", "provider_specific"]
    availability: Literal["suggested", "bundled", "external_reference"]


class Palette(Model):
    background: str
    foreground: str
    accent: str


class Identity(Model):
    id: str
    title: str
    version: str
    palette: Palette
    description: str = ""
    author: str | None = None
    source: str | None = None
    tags: list[str] = Field(default_factory=list)


class GenerationPolicy(Model):
    phone_live_required: Literal[True]
    watch_live_required: Literal[True]
    fetch_missing_optional_components: Literal[False]


class Phone(Model):
    live_wallpaper: Asset | None = None
    static_wallpaper: Asset | None = None
    widgets: list[Component] = Field(default_factory=list)
    layout: dict[str, Any] | None = None
    icons: dict[str, Any] | None = None
    typography: dict[str, Any] | None = None
    lock_screen: dict[str, Any] | None = None
    sounds: dict[str, Any] | None = None
    system_style: dict[str, Any] | None = None


class Watch(Model):
    live_background: Asset | None = None
    static_background: Asset | None = None
    preview_video: Asset | None = None
    clock: dict[str, Any] | None = None
    complication_slots: list[Complication] = Field(default_factory=list)
    tiles: list[Component] = Field(default_factory=list)
    aod: dict[str, Any] | None = None
    face_package: dict[str, Any] | None = None


class Manifest(Model):
    schema_version: str
    theme: Identity
    generation_policy: GenerationPolicy
    phone: Phone
    watch: Watch
    dependencies: list[dict[str, Any]] = Field(default_factory=list)
    previews: list[Asset] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)


class Targets(Model):
    phone: bool = True
    watch: bool = True


class Override(Model):
    component_id: str
    enabled: bool
    provider_component: str | None = None
    slot_category: str | None = None


COMPONENT = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*/[A-Za-z0-9_.$]+$")


class Selection(Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    theme_id: str
    theme_version: str
    targets: Targets = Field(default_factory=Targets)
    overrides: list[Override] = Field(default_factory=list)
    device_bindings: dict[str, dict[str, str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_overrides(self):
        ids = [v.component_id for v in self.overrides]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate component selection")
        for value in self.overrides:
            if value.provider_component and not COMPONENT.fullmatch(
                value.provider_component
            ):
                raise ValueError("Invalid provider component name")
        for bindings in self.device_bindings.values():
            if any(not COMPONENT.fullmatch(v) for v in bindings.values()):
                raise ValueError("Invalid device provider binding")
        return self


class PlanInput(Model):
    devices: dict[Literal["phone", "watch"], str | None] = Field(default_factory=dict)


class StartJob(Model):
    plan_id: str


class Acknowledge(Model):
    outcome: Literal["applied", "skipped"]
    confirmed_on_device: bool = False


ItemStatus = Literal[
    "applied", "pending_user_action", "failed", "skipped", "unsupported"
]


class ApplyItem(Model):
    id: str
    device: Literal["phone", "watch"]
    category: str
    label: str
    serial: str | None = None
    provider: str | None = None
    provider_label: str | None = None
    asset: str | None = None
    mode: Literal["user_action", "unsupported"] = "user_action"
    reason: str = ""
    instructions: list[str] = Field(default_factory=list)
    status: ItemStatus | None = None
    verification: Literal["none", "user_reported", "launcher_binding"] = "none"
    staged_files: list[dict[str, Any]] = Field(default_factory=list)
    attempts: int = 0
