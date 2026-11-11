import React, { useEffect, useRef, useState } from "react";
import type {
  Asset,
  Capabilities,
  Device,
  Job,
  Manifest,
  Override,
  Pack,
  Plan,
  Selection,
  ThemeComponent,
} from "./packTypes";
import "./pack.css";

export const PACK_API = "http://127.0.0.1:8000/api/packs";
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(PACK_API + path, init);
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail ?? "Request failed"),
    );
  return data;
}
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
const assetUrl = (id: string, asset?: Asset) =>
  asset
    ? `${PACK_API}/${id}/assets/${asset.path.split("/").map(encodeURIComponent).join("/")}`
    : undefined;
const readable = (value: string) => value.replaceAll("_", " ");

export function PackStudio({ devices = [] }: { devices?: Device[] }) {
  const [packs, setPacks] = useState<Pack[]>([]);
  const [packId, setPackId] = useState("");
  const [pack, setPack] = useState<Pack | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [serials, setSerials] = useState<Record<"phone" | "watch", string>>({
    phone: "",
    watch: "",
  });
  const [caps, setCaps] = useState<Record<string, Capabilities>>({});
  const [capErrors, setCapErrors] = useState<Record<string, string>>({});
  const [plan, setPlan] = useState<Plan | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [history, setHistory] = useState<Job[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState(
    () =>
      typeof matchMedia === "undefined" ||
      !matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const region = useRef<HTMLElement>(null);

  async function reload(prefer?: string) {
    const data = await request<{ packs: Pack[] }>("");
    setPacks(data.packs);
    setPackId(
      (old) =>
        prefer ??
        (data.packs.some((p) => p.id === old)
          ? old
          : (data.packs[0]?.id ?? "")),
    );
  }
  useEffect(() => {
    reload().catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    setPlan(null);
    setPack(null);
    setSelection(null);
    setJob(null);
    setHistory([]);
    if (!packId) return;
    const controller = new AbortController();
    Promise.all([
      request<Pack>("/" + packId, { signal: controller.signal }),
      request<{ jobs: Job[] }>("/jobs", { signal: controller.signal }),
    ])
      .then(([p, js]) => {
        setPack(p);
        setSelection(p.selection ?? null);
        const own = js.jobs.filter((j) => j.pack_id === packId);
        setHistory(own);
        setJob(own[0] ?? null);
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [packId]);
  useEffect(() => {
    const controller = new AbortController();
    setCaps({});
    setCapErrors({});
    setPlan(null);
    (["phone", "watch"] as const).forEach((role) => {
      const serial = serials[role];
      if (!serial) return;
      request<Capabilities>(
        "/devices/" + encodeURIComponent(serial) + "/capabilities",
        { signal: controller.signal },
      )
        .then((d) => setCaps((v) => ({ ...v, [role]: d })))
        .catch((e) => {
          if (e.name !== "AbortError")
            setCapErrors((v) => ({ ...v, [role]: e.message }));
        });
    });
    return () => controller.abort();
  }, [serials.phone, serials.watch]);
  useEffect(() => {
    if (!job?.running) return;
    let stopped = false;
    const timer = setInterval(
      () =>
        request<Job>("/jobs/" + job.id)
          .then((value) => {
            if (!stopped) setJob(value);
          })
          .catch((e) => {
            if (!stopped) setError(e.message);
          }),
      1000,
    );
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [job?.id, job?.running]);
  useEffect(() => {
    if (job) setHistory((v) => [job, ...v.filter((old) => old.id !== job.id)]);
  }, [job]);
  useEffect(() => {
    region.current?.querySelectorAll("video").forEach((video) => {
      if (playing) video.play()?.catch(() => {});
      else video.pause();
    });
  }, [playing, pack]);

  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function importZip(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    await action(async () => {
      const data = new FormData();
      data.append("file", file);
      const imported = await request<{ packs: Pack[] }>("/import", {
        method: "POST",
        body: data,
      });
      await reload(imported.packs[0]?.id);
      setMessage(
        `Imported ${imported.packs.length} theme pack(s). No device settings changed.`,
      );
    });
  }
  async function save(next: Selection) {
    await action(async () => {
      setSelection(await post<Selection>(`/${packId}/selection`, next));
      setPlan(null);
    });
  }
  function updateComponent(c: ThemeComponent, patch: Partial<Override>) {
    if (!selection) return;
    const prior = selection.overrides.find((o) => o.component_id === c.id);
    const next: Override = {
      component_id: c.id,
      enabled: prior?.enabled ?? c.default_enabled,
      ...prior,
      ...patch,
    };
    void save({
      ...selection,
      overrides: [
        ...selection.overrides.filter((o) => o.component_id !== c.id),
        next,
      ],
    });
  }
  function chooseProvider(c: ThemeComponent, value: string) {
    if (!selection) return;
    const serial = serials[c.device];
    if (!serial) return;
    const bindings = {
      ...selection.device_bindings,
      [serial]: { ...(selection.device_bindings?.[serial] ?? {}) },
    };
    if (value) bindings[serial][c.id] = value;
    else delete bindings[serial][c.id];
    const overrides = selection.overrides.map((o) =>
      o.component_id === c.id ? { ...o, provider_component: null } : o,
    );
    void save({ ...selection, device_bindings: bindings, overrides });
  }
  const title = pack?.manifest.theme.title ?? "Choose a theme";
  const controlsDisabled = busy || !!job?.running;
  function optional(c: ThemeComponent) {
    const override = selection?.overrides.find((o) => o.component_id === c.id);
    const enabled = override?.enabled ?? c.default_enabled;
    const category = override?.slot_category ?? c.default_category;
    const provider =
      c.options?.find((o) => o.category === category)?.provider ?? c.provider;
    const query = provider
      ? caps[c.device]?.providers[provider.platform]
      : undefined;
    const serial = serials[c.device];
    const binding =
      selection?.device_bindings?.[serial]?.[c.id] ??
      override?.provider_component ??
      provider?.component ??
      "";
    return (
      <div className="component-row" key={c.id}>
        <label>
          <input
            type="checkbox"
            checked={enabled}
            disabled={controlsDisabled}
            onChange={(e) => updateComponent(c, { enabled: e.target.checked })}
          />
          <strong>{c.label}</strong>
          <span className="pack-tag">{c.availability ?? "optional"}</span>
        </label>
        {enabled && c.options && (
          <label className="pack-field">
            Information for {c.label}
            <select
              aria-label={`Information for ${c.label}`}
              value={category ?? ""}
              disabled={controlsDisabled}
              onChange={(e) =>
                updateComponent(c, {
                  slot_category: e.target.value,
                  provider_component: null,
                })
              }
            >
              {c.options.map((o) => (
                <option key={o.category} value={o.category}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {enabled && provider && (
          <>
            <label className="pack-field">
              Provider for {c.label}
              <select
                aria-label={`Provider for ${c.label}`}
                value={binding}
                disabled={controlsDisabled || !serial}
                onChange={(e) => chooseProvider(c, e.target.value)}
              >
                <option value="">Choose/configure on device</option>
                {binding && !query?.components.includes(binding) && (
                  <option value={binding}>
                    {binding} — unavailable or unverified
                  </option>
                )}
                {query?.components.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <small>
              {query?.status === "queried"
                ? `${query.components.length} installed provider(s) discovered; data-type compatibility still needs checking.`
                : "Provider availability is unverified. Select a connected device or choose in its system picker."}
            </small>
          </>
        )}
        {enabled && c.dependency_ids && c.dependency_ids.length > 0 && (
          <small>
            Dependencies: {c.dependency_ids.join(", ")}. Nothing is installed
            automatically.
          </small>
        )}
      </div>
    );
  }
  function deviceSection(role: "phone" | "watch") {
    if (!selection || !pack) return null;
    const required =
      role === "phone"
        ? pack.manifest.phone.live_wallpaper
        : pack.manifest.watch.live_background;
    return (
      <section
        className="pack-device"
        aria-label={`${role} configuration`}
        key={role}
      >
        <div className="pack-device-heading">
          <h3>{role === "phone" ? "Phone" : "Watch"}</h3>
          <label>
            <input
              type="checkbox"
              aria-label={`Apply to ${role}`}
              checked={selection.targets[role]}
              disabled={controlsDisabled}
              onChange={(e) =>
                void save({
                  ...selection,
                  targets: { ...selection.targets, [role]: e.target.checked },
                })
              }
            />{" "}
            Apply to this device
          </label>
        </div>
        <label className="pack-field">
          Target {role}
          <select
            aria-label={`Target ${role}`}
            value={serials[role]}
            disabled={controlsDisabled}
            onChange={(e) =>
              setSerials((v) => ({ ...v, [role]: e.target.value }))
            }
          >
            <option value="">Choose an authorized device</option>
            {devices.map((d) => (
              <option
                key={d.serial}
                value={d.serial}
                disabled={d.status !== "device"}
              >
                {d.model_hint?.replaceAll("_", " ") || d.serial} · {d.serial} ·{" "}
                {d.status}
              </option>
            ))}
          </select>
        </label>
        {caps[role] && (
          <small>
            Detected: {caps[role].model} · {caps[role].kind} ·{" "}
            {caps[role].launcher}
          </small>
        )}
        {capErrors[role] && (
          <p role="alert" className="pack-error">
            {capErrors[role]}
          </p>
        )}
        {caps[role] && caps[role].kind !== role && (
          <p className="pack-error">
            Select a {role}; this connection is a {caps[role].kind}.
          </p>
        )}
        <div className="pack-required">
          <strong>Live {role === "phone" ? "wallpaper" : "background"}</strong>
          <span className="pack-tag">Required in pack</span>
          <small>
            {required
              ? `${required.width} × ${required.height} · ${required.duration_seconds}s · ${required.mime_type}`
              : "Missing — incomplete draft"}
          </small>
        </div>
        {(pack.components ?? [])
          .filter((c) => c.device === role && !c.required)
          .map(optional)}
        {role === "watch" && (
          <p className="pack-help">
            Complications belong to the face; Tiles are separate pages. Animated
            artwork needs a compatible built face. AOD uses a separate
            static/minimal clock state.
          </p>
        )}
      </section>
    );
  }
  function watchPreview(manifest: Manifest) {
    const video = assetUrl(packId, manifest.watch.preview_video);
    if (video)
      return (
        <video
          aria-label="Watch animation preview"
          src={video}
          muted
          loop
          playsInline
          controls
          autoPlay={playing}
          poster={assetUrl(packId, manifest.watch.static_background)}
        />
      );
    const image = playing
      ? manifest.watch.live_background
      : manifest.watch.static_background;
    return image ? (
      <img
        src={assetUrl(packId, image)}
        alt={playing ? "Watch animated background" : "Paused watch preview"}
      />
    ) : (
      <span>{playing ? "No watch animation" : "Animation paused"}</span>
    );
  }
  return (
    <section
      ref={region}
      className="panel pack-studio"
      aria-label="Theme pack studio"
    >
      <div className="panel-top">
        <div>
          <h2>Theme packs</h2>
          <p>
            Coordinated motion, widgets and complications. Apply only what you
            choose.
          </p>
        </div>
        <label
          className={`button primary upload-button ${busy ? "disabled" : ""}`}
        >
          Import theme ZIP
          <input
            aria-label="Import theme ZIP"
            type="file"
            accept=".zip,application/zip"
            disabled={busy}
            onChange={importZip}
          />
        </label>
      </div>
      {error && (
        <div className="pack-error" role="alert">
          {error}
        </div>
      )}
      {message && (
        <div className="pack-message" role="status">
          {message}
        </div>
      )}
      <div className="pack-list">
        {packs.map((p) => (
          <button
            key={p.id}
            className={`pack-card ${p.id === packId ? "selected" : ""}`}
            disabled={busy}
            onClick={() => setPackId(p.id)}
          >
            <span className="pack-swatches">
              {Object.values(p.manifest.theme.palette).map((color, i) => (
                <i key={i} style={{ background: color }} />
              ))}
            </span>
            <strong>{p.manifest.theme.title}</strong>
            <small>
              {p.manifest.theme.version} ·{" "}
              {p.complete
                ? "Complete · phone + watch live"
                : "Incomplete draft"}
            </small>
          </button>
        ))}
      </div>
      {!packs.length && (
        <div className="empty">
          <strong>Bring your phone and watch together</strong>
          <p>
            Import a ZIP containing a versioned manifest, phone MP4 and animated
            watch WebP/GIF. Optional extras can be omitted.
          </p>
        </div>
      )}
      <button
        className="button outlined small"
        disabled={busy}
        onClick={() =>
          void action(async () => {
            const r = await post<{ packs: Pack[] }>("/migrate-legacy");
            await reload(r.packs[0]?.id);
            setMessage(
              "Copied legacy themes into the pack library. Originals are preserved; still-only themes remain drafts.",
            );
          })
        }
      >
        Import existing legacy themes
      </button>
      {pack && selection && (
        <>
          <div className="pack-title">
            <div>
              <h3>{title}</h3>
              <p>{pack.manifest.theme.description}</p>
            </div>
            <div className="pack-toolbar">
              <button
                className="button outlined small"
                onClick={() => setPlaying((v) => !v)}
              >
                {playing ? "Pause previews" : "Play previews"}
              </button>
              <a
                className="button outlined small"
                href={`${PACK_API}/${packId}/export`}
              >
                Export pack
              </a>
              <button
                className="button outlined small"
                disabled={controlsDisabled}
                onClick={() =>
                  void action(async () => {
                    setSelection(
                      await post<Selection>(`/${packId}/selection/reset`),
                    );
                    setPlan(null);
                    setMessage(
                      "Restored this theme’s defaults. No device settings changed.",
                    );
                  })
                }
              >
                Reset to theme defaults
              </button>
            </div>
          </div>
          {!pack.complete && (
            <p className="pack-error">
              Incomplete draft: {pack.issues.join("; ")}. Both live animations
              are required before application.
            </p>
          )}
          <div
            className="pack-previews"
            style={{ background: pack.manifest.theme.palette.background }}
          >
            <div className="pack-preview-phone">
              {pack.manifest.phone.live_wallpaper ? (
                <video
                  aria-label="Phone animation preview"
                  src={assetUrl(packId, pack.manifest.phone.live_wallpaper)}
                  muted
                  loop
                  playsInline
                  controls
                  autoPlay={playing}
                  poster={assetUrl(
                    packId,
                    pack.manifest.phone.static_wallpaper,
                  )}
                />
              ) : (
                <span>No phone live wallpaper</span>
              )}
            </div>
            <div className="pack-preview-watch">
              {watchPreview(pack.manifest)}
            </div>
            <p>
              Live information is configured on the device; no sample readings
              are baked into this artwork.
            </p>
          </div>
          <div className="pack-devices">
            {deviceSection("phone")}
            {deviceSection("watch")}
          </div>
          {!!pack.manifest.dependencies?.length && (
            <details className="pack-dependencies">
              <summary>Dependency references — no automatic installs</summary>
              <pre>{JSON.stringify(pack.manifest.dependencies, null, 2)}</pre>
            </details>
          )}
          <div className="pack-toolbar">
            <button
              className="button primary"
              disabled={controlsDisabled || !pack.complete}
              onClick={() =>
                void action(async () =>
                  setPlan(
                    await post<Plan>(`/${packId}/plan`, { devices: serials }),
                  ),
                )
              }
            >
              Review selected components
            </button>
            <span className="pack-help">
              Saved choices override theme defaults. Review before starting.
            </span>
          </div>
          {plan && (
            <section className="pack-review" aria-label="Application review">
              <h3>Application review</h3>
              <p>
                {plan.items.length} selected component(s). Settings requiring
                device interaction stay pending.
              </p>
              {plan.items.map((i) => (
                <div key={i.id} className="pack-review-item">
                  <strong>{i.label}</strong>
                  <span>
                    {i.device} · {i.serial || "No target selected"} ·{" "}
                    {readable(i.mode)}
                  </span>
                  <small>{i.reason}</small>
                  {i.provider && <small>Provider: {i.provider}</small>}
                  <details>
                    <summary>Configuration and device steps</summary>
                    <ol>
                      {i.instructions.map((line, n) => (
                        <li key={n}>{line}</li>
                      ))}
                    </ol>
                  </details>
                </div>
              ))}
              <button
                className="button primary"
                disabled={controlsDisabled || !plan.items.length}
                onClick={() =>
                  void action(async () => {
                    setJob(
                      await post<Job>(`/${packId}/jobs`, { plan_id: plan.id }),
                    );
                    setPlan(null);
                  })
                }
              >
                Start selected actions
              </button>
            </section>
          )}
          {history.length > 1 && (
            <label className="pack-field">
              Apply history
              <select
                aria-label="Apply history"
                value={job?.id ?? ""}
                onChange={(e) =>
                  setJob(history.find((j) => j.id === e.target.value) ?? null)
                }
              >
                {history.map((j) => (
                  <option value={j.id} key={j.id}>
                    {j.created_at} · {readable(j.status)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {job && (
            <section className="pack-job" aria-label="Apply job">
              <div className="pack-device-heading">
                <h3>Apply job · {readable(job.status)}</h3>
                <div className="pack-toolbar">
                  {job.items.some((i) => i.status === "failed") && (
                    <button
                      className="button outlined small"
                      disabled={busy || job.running}
                      onClick={() =>
                        void action(async () =>
                          setJob(await post<Job>(`/jobs/${job.id}/retry`)),
                        )
                      }
                    >
                      Retry failed items
                    </button>
                  )}
                  <button
                    className="button outlined small"
                    disabled={
                      busy || job.cancel_requested || job.status === "completed"
                    }
                    onClick={() =>
                      void action(async () =>
                        setJob(await post<Job>(`/jobs/${job.id}/cancel`)),
                      )
                    }
                  >
                    {job.cancel_requested && job.running
                      ? "Cancelling after current action…"
                      : "Cancel remaining actions"}
                  </button>
                </div>
              </div>
              {!job.running &&
                !job.items.some(
                  (i) =>
                    i.status === "applied" ||
                    i.status === "pending_user_action",
                ) &&
                job.items.some((i) => i.staged_files.length > 0) && (
                  <button
                    className="button outlined small"
                    disabled={busy}
                    onClick={() =>
                      void action(async () =>
                        setJob(
                          await post<Job>(`/jobs/${job.id}/cleanup-staging`),
                        ),
                      )
                    }
                  >
                    Remove unused staged copies
                  </button>
                )}
              {job.items.map((i) => (
                <details
                  className="pack-job-item"
                  key={i.id}
                  open={i.status === "pending_user_action"}
                >
                  <summary>
                    <strong>{i.label}</strong>
                    <span className={`pack-status ${i.status ?? "queued"}`}>
                      {readable(i.status ?? "queued")}
                    </span>
                  </summary>
                  <p>{i.reason}</p>
                  <ol>
                    {i.instructions.map((line, n) => (
                      <li key={n}>{line}</li>
                    ))}
                  </ol>
                  {i.staged_files.map((file, n) => (
                    <p key={n}>
                      Staged: <code>{file.path}</code> ·{" "}
                      {file.transfer_verified
                        ? "transfer checksum verified"
                        : "transfer not independently verified"}
                      . This does not verify application.{" "}
                      {file.removed
                        ? "Removed owned staged copy."
                        : (file.cleanup_error ?? "")}
                    </p>
                  ))}
                  {i.verification === "user_reported" && (
                    <small>
                      Completion reported by you; not automatically verified by
                      ThemeLab.
                    </small>
                  )}
                  {i.status === "pending_user_action" && !job.running && (
                    <div className="pack-toolbar">
                      <button
                        className="button primary small"
                        disabled={busy}
                        onClick={() =>
                          void action(async () =>
                            setJob(
                              await post<Job>(
                                `/jobs/${job.id}/items/${i.id}/confirm`,
                                {
                                  outcome: "applied",
                                  confirmed_on_device: true,
                                },
                              ),
                            ),
                          )
                        }
                      >
                        I confirmed this on the device
                      </button>
                      <button
                        className="button outlined small"
                        disabled={busy}
                        onClick={() =>
                          void action(async () =>
                            setJob(
                              await post<Job>(
                                `/jobs/${job.id}/items/${i.id}/confirm`,
                                { outcome: "skipped" },
                              ),
                            ),
                          )
                        }
                      >
                        Skip this item
                      </button>
                    </div>
                  )}
                </details>
              ))}
            </section>
          )}
        </>
      )}
    </section>
  );
}

export default PackStudio;
