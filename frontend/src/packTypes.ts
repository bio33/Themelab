export interface Asset {
  path: string;
  mime_type: string;
  sha256?: string;
  width?: number;
  height?: number;
  duration_seconds?: number;
  fps?: number;
  loop?: boolean;
  audio?: boolean;
}
export interface Provider {
  platform: "android_appwidget" | "wear_os_complication" | "wear_os_tile";
  category: string;
  strategy: "discover_on_device";
  component?: string | null;
}
export interface ThemeComponent {
  id: string;
  device: "phone" | "watch";
  label: string;
  category: string;
  default_enabled: boolean;
  required?: boolean;
  availability?: "suggested" | "bundled" | "external_reference";
  provider?: Provider;
  options?: { category: string; label: string; provider: Provider }[];
  default_category?: string;
  placement?: Record<string, unknown>;
  style?: Record<string, unknown>;
  dependency_ids?: string[];
  apply_mode?: string;
  bounds?: { x: number; y: number; width: number; height: number };
  slot_id?: number;
  supported_types?: string[];
  preset_asset?: Asset;
}
export interface Manifest {
  schema_version: string;
  theme: {
    id: string;
    title: string;
    version: string;
    description?: string;
    author?: string;
    source?: string;
    tags?: string[];
    palette: { background: string; foreground: string; accent: string };
  };
  generation_policy: {
    phone_live_required: true;
    watch_live_required: true;
    fetch_missing_optional_components: false;
  };
  phone: {
    live_wallpaper?: Asset;
    static_wallpaper?: Asset;
    widgets?: Omit<ThemeComponent, "device">[];
    layout?: Record<string, unknown>;
    icons?: Record<string, unknown>;
    typography?: Record<string, unknown>;
    lock_screen?: Record<string, unknown>;
    sounds?: Record<string, unknown>;
    system_style?: Record<string, unknown>;
  };
  watch: {
    live_background?: Asset;
    static_background?: Asset;
    preview_video?: Asset;
    clock?: Record<string, unknown>;
    complication_slots?: ComplicationSlot[];
    tiles?: Omit<ThemeComponent, "device">[];
    aod?: Record<string, unknown>;
    face_package?: Record<string, unknown>;
  };
  dependencies?: Record<string, unknown>[];
  previews?: Asset[];
  extensions?: Record<string, unknown>;
}
export interface ComplicationSlot {
  id: string;
  slot_id: number;
  bounds: { x: number; y: number; width: number; height: number };
  supported_types: string[];
  default_enabled: boolean;
  default_category: string;
  options: { category: string; label: string; provider: Provider }[];
  style: Record<string, unknown>;
  apply_mode: "user_action" | "provider_specific";
  availability: "suggested" | "bundled" | "external_reference";
}
export interface Override {
  component_id: string;
  enabled: boolean;
  provider_component?: string | null;
  slot_category?: string | null;
}
export interface Selection {
  schema_version: "1.0.0";
  theme_id: string;
  theme_version: string;
  targets: Record<"phone" | "watch", boolean>;
  overrides: Override[];
  device_bindings: Record<string, Record<string, string>>;
}
export interface Pack {
  id: string;
  manifest: Manifest;
  complete: boolean;
  issues: string[];
  source: string;
  created_at: string;
  components?: ThemeComponent[];
  selection?: Selection;
}
export interface Device {
  serial: string;
  status: string;
  model_hint?: string;
}
export interface Capabilities {
  kind: string;
  model: string;
  launcher: string;
  providers: Record<
    string,
    {
      status: string;
      components: string[];
      types_verified: boolean;
      reason?: string;
    }
  >;
}
export type ItemStatus =
  "applied" | "pending_user_action" | "failed" | "skipped" | "unsupported";
export interface ApplyItem {
  id: string;
  device: "phone" | "watch";
  category: string;
  label: string;
  serial: string | null;
  provider: string | null;
  asset: string | null;
  mode: string;
  reason: string;
  instructions: string[];
  status: ItemStatus | null;
  verification: "none" | "user_reported";
  staged_files: {
    path: string;
    transfer_verified: boolean;
    created_by_job: boolean;
    removed?: boolean;
    cleanup_error?: string;
  }[];
  attempts: number;
}
export interface Plan {
  id: string;
  pack_id: string;
  items: ApplyItem[];
  devices: Record<string, string | null>;
}
export interface Job {
  id: string;
  pack_id: string;
  status: string;
  items: ApplyItem[];
  running: boolean;
  cancel_requested: boolean;
  created_at: string;
}
