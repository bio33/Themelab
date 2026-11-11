import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PackStudio from "./PackStudio";
import type { ApplyItem, Pack, Selection } from "./packTypes";

const widget = {
  id: "clock-widget",
  device: "phone" as const,
  label: "Analog clock",
  category: "analog_clock",
  default_enabled: true,
  availability: "suggested" as const,
  provider: {
    platform: "android_appwidget" as const,
    category: "clock",
    strategy: "discover_on_device" as const,
  },
};
const pack: Pack = {
  id: "abc",
  complete: true,
  issues: [],
  source: "standard",
  created_at: "2026-11-11",
  manifest: {
    schema_version: "1.0.0",
    theme: {
      id: "test-theme",
      title: "Test Theme",
      version: "1.0.0",
      palette: {
        background: "#111111",
        foreground: "#FFFFFF",
        accent: "#FFCC00",
      },
    },
    generation_policy: {
      phone_live_required: true,
      watch_live_required: true,
      fetch_missing_optional_components: false,
    },
    phone: {
      live_wallpaper: { path: "phone.mp4", mime_type: "video/mp4" },
      widgets: [widget],
    },
    watch: {
      live_background: { path: "watch.webp", mime_type: "image/webp" },
      static_background: { path: "watch.png", mime_type: "image/png" },
    },
  },
  components: [
    {
      id: "phone-live",
      device: "phone",
      label: "Phone live wallpaper",
      category: "live",
      required: true,
      default_enabled: true,
    },
    {
      id: "watch-live",
      device: "watch",
      label: "Watch live background",
      category: "live",
      required: true,
      default_enabled: true,
    },
    widget,
  ],
};
const defaults = (): Selection => ({
  schema_version: "1.0.0",
  theme_id: "test-theme",
  theme_version: "1.0.0",
  targets: { phone: true, watch: true },
  overrides: [],
  device_bindings: {},
});
let saved: Selection;
let calls: { path: string; body: unknown }[];
let importError = false;
const item = (id: string, device: "phone" | "watch"): ApplyItem => ({
  id,
  device,
  category: "live",
  label: device === "phone" ? "Phone live wallpaper" : "Watch live background",
  serial: device.toUpperCase(),
  provider: null,
  asset: null,
  mode: "user_action",
  reason: "Confirm the actual device result.",
  instructions: ["Choose the media on the device."],
  status: "pending_user_action",
  verification: "none",
  staged_files: [],
  attempts: 1,
});

beforeEach(() => {
  saved = defaults();
  calls = [];
  importError = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const path = String(input).replace("http://127.0.0.1:8000/api/packs", "");
      const body =
        typeof init.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ path, body });
      let data: unknown;
      let ok = true;
      if (path === "") data = { packs: [pack] };
      else if (path === "/jobs") data = { jobs: [] };
      else if (path === "/abc") data = { ...pack, selection: saved };
      else if (path === "/abc/selection") {
        saved = body;
        data = saved;
      } else if (path === "/abc/selection/reset") {
        saved = defaults();
        data = saved;
      } else if (path.includes("/capabilities"))
        data = {
          kind: path.includes("PHONE") ? "phone" : "watch",
          model: "Connected device",
          launcher: "org.launcher/.Home",
          providers: {
            android_appwidget: {
              status: "queried",
              components: ["org.clock/.Widget"],
              types_verified: false,
            },
          },
        };
      else if (path === "/abc/plan")
        data = {
          id: "plan",
          pack_id: "abc",
          devices: body.devices,
          items: [
            ...(saved.targets.phone ? [item("phone-live", "phone")] : []),
            ...(saved.targets.watch ? [item("watch-live", "watch")] : []),
          ],
        };
      else if (path === "/abc/jobs")
        data = {
          id: "job",
          pack_id: "abc",
          status: "pending_user_action",
          running: false,
          cancel_requested: false,
          items: [item("phone-live", "phone")],
          created_at: "today",
        };
      else if (path.includes("/confirm"))
        data = {
          id: "job",
          pack_id: "abc",
          status: "completed",
          running: false,
          cancel_requested: false,
          items: [
            {
              ...item("phone-live", "phone"),
              status: "applied",
              verification: "user_reported",
            },
          ],
          created_at: "today",
        };
      else if (path === "/import") {
        if (importError) {
          ok = false;
          data = { detail: "Watch live background is required" };
        } else data = { packs: [pack] };
      } else throw new Error("Unexpected request " + path);
      return { ok, json: async () => structuredClone(data) } as Response;
    }),
  );
});

describe("theme-pack interaction", () => {
  it("renders true animated assets with loop controls and pauses watch preview", async () => {
    render(<PackStudio />);
    const video = await screen.findByLabelText("Phone animation preview");
    expect(video).toHaveAttribute("src", expect.stringContaining("phone.mp4"));
    expect(video).toHaveAttribute("loop");
    expect(video).toHaveAttribute("controls");
    expect(screen.getByAltText("Watch animated background")).toHaveAttribute(
      "src",
      expect.stringContaining("watch.webp"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Pause previews" }),
    );
    expect(screen.getByAltText("Paused watch preview")).toHaveAttribute(
      "src",
      expect.stringContaining("watch.png"),
    );
  });

  it("saves toggles separately and reset restores theme defaults", async () => {
    render(<PackStudio />);
    const toggle = await screen.findByRole("checkbox", {
      name: /Analog clock/,
    });
    await userEvent.click(toggle);
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(saved.overrides).toContainEqual(
      expect.objectContaining({ component_id: "clock-widget", enabled: false }),
    );
    expect(
      pack.components?.find((c) => c.id === "clock-widget")?.default_enabled,
    ).toBe(true);
    await userEvent.click(
      screen.getByRole("button", { name: "Reset to theme defaults" }),
    );
    await waitFor(() => expect(toggle).toBeChecked());
    expect(saved.overrides).toEqual([]);
  });

  it("uses independent targets and device-specific discovered provider bindings", async () => {
    render(
      <PackStudio
        devices={[
          { serial: "PHONE", status: "device", model_hint: "Galaxy phone" },
          { serial: "WATCH", status: "device", model_hint: "Galaxy watch" },
        ]}
      />,
    );
    await screen.findByRole("heading", { name: "Test Theme" });
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Target phone" }),
      "PHONE",
    );
    const provider = await screen.findByRole("combobox", {
      name: "Provider for Analog clock",
    });
    await screen.findByRole("option", { name: "org.clock/.Widget" });
    await userEvent.selectOptions(provider, "org.clock/.Widget");
    await waitFor(() =>
      expect(saved.device_bindings.PHONE?.["clock-widget"]).toBe(
        "org.clock/.Widget",
      ),
    );
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Apply to watch" }),
    );
    await waitFor(() => expect(saved.targets.watch).toBe(false));
    await userEvent.click(
      screen.getByRole("button", { name: "Review selected components" }),
    );
    const review = await screen.findByRole("region", {
      name: "Application review",
    });
    expect(review).toHaveTextContent("Phone live wallpaper");
    expect(review).not.toHaveTextContent("Watch live background");
  });

  it("never turns requested actions into success until explicit device confirmation", async () => {
    render(<PackStudio />);
    await screen.findByRole("heading", { name: "Test Theme" });
    await userEvent.click(
      screen.getByRole("button", { name: "Review selected components" }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Start selected actions" }),
    );
    expect(
      await screen.findByRole("heading", {
        name: "Apply job · pending user action",
      }),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "I confirmed this on the device" }),
    );
    expect(
      await screen.findByText(
        "Completion reported by you; not automatically verified by ThemeLab.",
      ),
    ).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/confirm"))?.body).toEqual({
      outcome: "applied",
      confirmed_on_device: true,
    });
  });

  it("shows invalid import errors without changing device settings", async () => {
    importError = true;
    render(<PackStudio />);
    await screen.findByRole("heading", { name: "Test Theme" });
    await userEvent.upload(
      screen.getByLabelText("Import theme ZIP"),
      new File(["bad"], "theme.zip", { type: "application/zip" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Watch live background is required",
    );
    expect(calls.some((c) => c.path === "/abc/jobs")).toBe(false);
  });
});
