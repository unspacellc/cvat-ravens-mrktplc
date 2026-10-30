import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, useLabeling } from "./api";
import { Async, Empty } from "./states";
import type { LabelDef, MediaItem, PreviewResult, SchemaVersion } from "./types";

const STEPS = ["Media", "Labels", "Details", "Preview", "Create"] as const;
const LABEL_TYPES = ["rectangle", "polygon", "polyline", "points", "ellipse", "cuboid", "skeleton", "mask", "tag"];

interface Draft {
  assets: string[];
  schemaId: string; // existing version, or "new"
  labels: LabelDef[];
  name: string;
  instructions: string;
  priority: string;
  dueDate: string;
  segmentSize: string;
}

const EMPTY: Draft = { assets: [], schemaId: "", labels: [{ name: "", type: "rectangle" }], name: "", instructions: "", priority: "0", dueDate: "", segmentSize: "" };

const fieldCls = "w-full rounded-md border border-border bg-card px-3 py-2 text-sm";

function stepProblem(step: number, d: Draft): string | null {
  if (step === 0 && d.assets.length === 0) return "Choose at least one clip.";
  if (step === 1) {
    if (d.schemaId && d.schemaId !== "new") return null;
    const names = d.labels.map((l) => l.name.trim());
    if (names.some((n) => !n)) return "Every label needs a name.";
    if (new Set(names).size !== names.length) return "Label names must be unique.";
  }
  if (step === 2) {
    if (!d.name.trim()) return "Give the batch a name.";
    const pr = Number(d.priority);
    if (!Number.isInteger(pr) || pr < 0 || pr > 100) return "Priority is a whole number from 0 to 100.";
    if (d.segmentSize && (!Number.isInteger(Number(d.segmentSize)) || Number(d.segmentSize) < 1)) return "Frames per job must be a whole number of at least 1.";
  }
  return null;
}

/** Five-step creation wizard. Nothing is written until the last step; the Idempotency-Key is fixed per wizard session. */
export function BatchWizard({ onCreated }: { onCreated: (batchId: string) => void }) {
  const { api } = useLabeling();
  const [step, setStep] = useState(0);
  const [d, setD] = useState<Draft>(EMPTY);
  const [problem, setProblem] = useState<string | null>(null);
  const key = useRef(crypto.randomUUID());
  const heading = useRef<HTMLHeadingElement>(null);
  const set = useCallback((patch: Partial<Draft>) => setD((p) => ({ ...p, ...patch })), []);

  useEffect(() => heading.current?.focus(), [step]); // keyboard users land on the new step, not at the top of the page

  const media = useQuery({ queryKey: ["labeling", "media"], queryFn: api.media });
  const schemas = useQuery({ queryKey: ["labeling", "schemas"], queryFn: api.schemas });
  const models = useQuery({ queryKey: ["labeling", "models"], queryFn: api.models, retry: false });
  const segment = d.segmentSize ? Number(d.segmentSize) : undefined;
  const preview = useQuery({
    queryKey: ["labeling", "preview", d.assets, segment],
    queryFn: () => api.preview(d.assets, segment),
    enabled: step >= 3 && d.assets.length > 0,
  });
  const create = useMutation({
    mutationFn: async () => {
      const schema_version_id = d.schemaId && d.schemaId !== "new" ? d.schemaId : (await api.createSchema(d.labels.map((l) => ({ ...l, name: l.name.trim() })))).id;
      return api.createBatch(
        {
          name: d.name.trim(), asset_ids: d.assets, schema_version_id, segment_size: segment, instructions: d.instructions.trim() || undefined,
          priority: Number(d.priority), due_date: d.dueDate ? new Date(`${d.dueDate}T23:59:59`).toISOString() : undefined,
        },
        key.current
      );
    },
    onSuccess: (r) => onCreated(r.batch_id),
  });

  const next = () => {
    const p = stepProblem(step, d);
    setProblem(p);
    if (!p) setStep(step + 1);
  };
  const manualOnly = models.status !== "success" || !(models.data?.items ?? []).some((m) => m.supported);

  return (
    <section aria-labelledby="wiz-h">
      <h1 id="wiz-h" className="text-xl font-semibold">
        New batch
      </h1>
      <ol className="mt-3 flex flex-wrap gap-2 text-sm" aria-label="Steps">
        {STEPS.map((s, i) => (
          <li key={s} aria-current={i === step ? "step" : undefined} className={`rounded-full border px-3 py-1 ${i === step ? "border-foreground font-medium" : "border-border text-muted-foreground"}`}>
            {i + 1}. {s}
          </li>
        ))}
      </ol>

      <div className="mt-4 rounded-md border border-border p-4">
        <h2 ref={heading} tabIndex={-1} className="mb-3 font-medium outline-none">
          {STEPS[step]}
        </h2>
        {problem ? (
          <p role="alert" className="mb-3 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger-soft-fg">
            {problem}
          </p>
        ) : null}

        {step === 0 ? (
          <Async query={media} label="Loading clips" empty={<Empty title="No clips available" hint="Add clips to your organization's media location first." />}>
            {(m) => <MediaStep items={m.items} chosen={d.assets} onChange={(assets) => set({ assets })} />}
          </Async>
        ) : null}

        {step === 1 ? (
          <Async query={schemas} label="Loading label sets" empty={null}>
            {(sv) => <LabelsStep versions={sv.items} schemaId={d.schemaId} labels={d.labels} onChange={set} />}
          </Async>
        ) : null}

        {step === 2 ? (
          <div className="space-y-3">
            <Field label="Batch name" id="b-name">
              <input id="b-name" className={fieldCls} value={d.name} onChange={(e) => set({ name: e.target.value })} maxLength={256} />
            </Field>
            <Field label="Instructions for labelers" id="b-ins" hint="Saved with the batch; editing later creates a new version.">
              <textarea id="b-ins" className={fieldCls} rows={5} value={d.instructions} onChange={(e) => set({ instructions: e.target.value })} />
            </Field>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Priority (0–100)" id="b-pri">
                <input id="b-pri" type="number" min={0} max={100} className={fieldCls} value={d.priority} onChange={(e) => set({ priority: e.target.value })} />
              </Field>
              <Field label="Due date" id="b-due">
                <input id="b-due" type="date" className={fieldCls} value={d.dueDate} onChange={(e) => set({ dueDate: e.target.value })} />
              </Field>
              <Field label="Frames per job" id="b-seg" hint="Leave empty for the default.">
                <input id="b-seg" type="number" min={1} className={fieldCls} value={d.segmentSize} onChange={(e) => set({ segmentSize: e.target.value })} />
              </Field>
            </div>
          </div>
        ) : null}

        {step === 3 ? (
          <Async query={preview} label="Estimating the split" empty={null}>
            {(p) => <PreviewStep p={p} manualOnly={manualOnly} />}
          </Async>
        ) : null}

        {step === 4 ? (
          <div className="space-y-2 text-sm">
            <p>
              <strong>{d.name}</strong>: {d.assets.length} clip{d.assets.length === 1 ? "" : "s"}, priority {d.priority}
              {d.dueDate ? `, due ${d.dueDate}` : ""}.
            </p>
            <p className="text-muted-foreground">{manualOnly ? "Labelers will annotate manually. Pre-labeling can be started later if a model becomes available." : "You can start pre-labeling from the batch page."}</p>
            {create.isError ? (
              <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-danger-soft-fg">
                {create.error instanceof ApiError ? create.error.message : "Could not create the batch. Try again; nothing is duplicated."}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="mt-4 flex gap-2">
        <button type="button" className="rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-50" disabled={step === 0 || create.isPending} onClick={() => { setProblem(null); setStep(step - 1); }}>
          Back
        </button>
        {step < STEPS.length - 1 ? (
          <button type="button" className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground" onClick={next}>
            Next
          </button>
        ) : (
          <button type="button" className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-60" disabled={create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? "Creating…" : "Create batch"}
          </button>
        )}
      </div>
    </section>
  );
}

function Field({ label, id, hint, children }: { label: string; id: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium">
        {label}
      </label>
      {children}
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function MediaStep({ items, chosen, onChange }: { items: MediaItem[]; chosen: string[]; onChange: (ids: string[]) => void }) {
  if (items.length === 0) return <Empty title="No clips available" hint="Add clips to your organization's media location first." />;
  const all = chosen.length === items.length;
  return (
    <fieldset>
      <legend className="sr-only">Clips</legend>
      <label className="mb-2 flex items-center gap-2 text-sm font-medium">
        <input type="checkbox" checked={all} onChange={() => onChange(all ? [] : items.map((i) => i.source_asset_id))} /> Select all ({items.length})
      </label>
      <ul className="divide-y divide-border rounded-md border border-border">
        {items.map((m) => (
          <li key={m.source_asset_id}>
            <label className="flex items-center gap-3 p-3 text-sm">
              <input
                type="checkbox"
                checked={chosen.includes(m.source_asset_id)}
                onChange={(e) => onChange(e.target.checked ? [...chosen, m.source_asset_id] : chosen.filter((x) => x !== m.source_asset_id))}
              />
              <span className="font-medium">{m.source_asset_id}</span>
              <span className="ml-auto text-muted-foreground">
                {m.frames} frames · {m.duration_s}s · {m.width}×{m.height}
              </span>
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}

function LabelsStep({ versions, schemaId, labels, onChange }: { versions: SchemaVersion[]; schemaId: string; labels: LabelDef[]; onChange: (p: Partial<Draft>) => void }) {
  useEffect(() => {
    if (!schemaId) onChange({ schemaId: versions[0]?.id ?? "new" }); // a choice is always made, so "Next" never validates an unseen default
  }, [schemaId, versions, onChange]);
  const isNew = schemaId === "new";
  return (
    <fieldset className="space-y-3">
      <legend className="sr-only">Label set</legend>
      {versions.map((v) => (
        <label key={v.id} className="flex items-start gap-2 text-sm">
          <input type="radio" name="schema" checked={schemaId === v.id} onChange={() => onChange({ schemaId: v.id })} />
          <span>
            Existing set {v.semver}: {v.labels.map((l) => l.name).join(", ")}
          </span>
        </label>
      ))}
      <label className="flex items-start gap-2 text-sm">
        <input type="radio" name="schema" checked={isNew} onChange={() => onChange({ schemaId: "new" })} />
        <span>Define a new label set</span>
      </label>
      {isNew ? <LabelEditor labels={labels} onChange={(l) => onChange({ labels: l, schemaId: "new" })} /> : null}
    </fieldset>
  );
}

function LabelEditor({ labels, onChange }: { labels: LabelDef[]; onChange: (l: LabelDef[]) => void }) {
  const update = (i: number, patch: Partial<LabelDef>) => onChange(labels.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  return (
    <div className="space-y-2 pl-6">
      {labels.map((l, i) => (
        <div key={i} className="flex gap-2">
          <input aria-label={`Label ${i + 1} name`} className={fieldCls} value={l.name} onChange={(e) => update(i, { name: e.target.value })} />
          <select aria-label={`Label ${i + 1} type`} className="rounded-md border border-border bg-card px-2 text-sm" value={l.type} onChange={(e) => update(i, { type: e.target.value })}>
            {LABEL_TYPES.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          <button type="button" className="rounded-md border border-border px-2 text-sm disabled:opacity-50" disabled={labels.length === 1} onClick={() => onChange(labels.filter((_, j) => j !== i))}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" className="rounded-md border border-border px-3 py-1 text-sm" onClick={() => onChange([...labels, { name: "", type: "rectangle" }])}>
        Add label
      </button>
    </div>
  );
}

function PreviewStep({ p, manualOnly }: { p: PreviewResult; manualOnly: boolean }) {
  return (
    <div className="space-y-3 text-sm">
      <p>
        <strong>{p.totals.tasks}</strong> task{p.totals.tasks === 1 ? "" : "s"} and <strong>{p.totals.jobs}</strong> job{p.totals.jobs === 1 ? "" : "s"} from {p.totals.frames} frames ({p.segment_size} frames per job).
      </p>
      <table className="w-full text-left">
        <caption className="sr-only">Split per clip</caption>
        <thead>
          <tr className="text-muted-foreground">
            <th className="py-1 pr-3 font-medium">Clip</th>
            <th className="py-1 pr-3 font-medium">Frames</th>
            <th className="py-1 font-medium">Jobs</th>
          </tr>
        </thead>
        <tbody>
          {p.clips.map((c) => (
            <tr key={c.source_asset_id} className="border-t border-border">
              <td className="py-1 pr-3">{c.source_asset_id}</td>
              <td className="py-1 pr-3">{c.frames}</td>
              <td className="py-1">{c.jobs}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {p.warnings.map((w) => (
        <p key={w} role="status" className="rounded-md bg-warn-soft px-3 py-2 text-warn-soft-fg">
          {w}
        </p>
      ))}
      <p className="text-muted-foreground">{manualOnly ? "No model service is configured: this batch will be labeled manually." : "A model is available: you can run pre-labeling after the batch is ready."}</p>
    </div>
  );
}
