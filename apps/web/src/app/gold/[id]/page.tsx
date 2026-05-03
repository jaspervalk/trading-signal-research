"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useEffect, useState } from "react";

import { Card, CardHeader } from "@/components/Card";
import { api, type GoldLabel } from "@/lib/api";
import { fmtDateTime } from "@/lib/utils";

const DIRECTIONS = ["", "long", "short", "unspecified"];
const ENTRY_TYPES = ["", "market", "limit", "trigger_above", "trigger_below", "unspecified"];
const TIMEFRAMES = ["", "day", "swing", "position", "unspecified"];

export default function GoldEditPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const goldId = parseInt(id, 10);
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["gold", goldId],
    queryFn: () => api.gold.get(goldId),
  });

  const [draft, setDraft] = useState<Partial<GoldLabel> | null>(null);

  useEffect(() => {
    if (data && draft === null) setDraft(data);
  }, [data, draft]);

  const save = useMutation({
    mutationFn: (vars: Partial<GoldLabel>) =>
      api.gold.update(goldId, {
        source_key: data!.source_key,
        source_text: vars.source_text ?? data!.source_text,
        expected_is_call: vars.expected_is_call ?? data!.expected_is_call,
        expected_ticker: vars.expected_ticker ?? null,
        expected_direction: vars.expected_direction ?? null,
        expected_entry_type: vars.expected_entry_type ?? null,
        expected_entry_price: vars.expected_entry_price ?? null,
        expected_target_price: vars.expected_target_price ?? null,
        expected_stop_price: vars.expected_stop_price ?? null,
        expected_timeframe: vars.expected_timeframe ?? null,
        notes: vars.notes ?? null,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["gold"] }),
  });

  if (isLoading || !data || draft === null) return <div className="text-sm text-[var(--muted-foreground)]">Loading…</div>;

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <Link href="/gold" className="text-sm text-[var(--muted-foreground)] hover:underline">← Gold set</Link>
        <h1 className="text-2xl font-semibold tracking-tight mt-1 font-mono">{data.source_key}</h1>
        <p className="text-xs text-[var(--muted-foreground)] mt-1">
          Created {fmtDateTime(data.created_at)} · Updated {fmtDateTime(data.updated_at)}
        </p>
      </div>

      <Card>
        <CardHeader title="Source text" subtitle="The text fed to the LLM extractor." />
        <textarea
          value={draft.source_text ?? ""}
          onChange={(e) => setDraft({ ...draft, source_text: e.target.value })}
          rows={6}
          className="w-full px-3 py-2 rounded border border-[var(--border)] bg-transparent text-sm font-mono"
        />
      </Card>

      <Card>
        <CardHeader title="Expected extraction" subtitle="What the extractor SHOULD produce on this text." />
        <div className="space-y-4">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.expected_is_call ?? false}
              onChange={(e) => setDraft({ ...draft, expected_is_call: e.target.checked })}
            />
            <span className="text-sm">This is a real trade call</span>
          </label>

          {draft.expected_is_call && (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
              <Field label="Ticker" value={draft.expected_ticker} onChange={(v) => setDraft({ ...draft, expected_ticker: v.toUpperCase() || null })} />
              <Select label="Direction" value={draft.expected_direction ?? ""} options={DIRECTIONS} onChange={(v) => setDraft({ ...draft, expected_direction: v || null })} />
              <Select label="Entry type" value={draft.expected_entry_type ?? ""} options={ENTRY_TYPES} onChange={(v) => setDraft({ ...draft, expected_entry_type: v || null })} />
              <FieldNum label="Entry price" value={draft.expected_entry_price} onChange={(v) => setDraft({ ...draft, expected_entry_price: v })} />
              <FieldNum label="Target price" value={draft.expected_target_price} onChange={(v) => setDraft({ ...draft, expected_target_price: v })} />
              <FieldNum label="Stop price" value={draft.expected_stop_price} onChange={(v) => setDraft({ ...draft, expected_stop_price: v })} />
              <Select label="Timeframe" value={draft.expected_timeframe ?? ""} options={TIMEFRAMES} onChange={(v) => setDraft({ ...draft, expected_timeframe: v || null })} />
            </div>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="Notes" />
        <textarea
          value={draft.notes ?? ""}
          onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
          rows={3}
          placeholder="Why is this a useful test case? What did the extractor get wrong on it before?"
          className="w-full px-3 py-2 rounded border border-[var(--border)] bg-transparent text-sm"
        />
      </Card>

      <div className="flex items-center gap-3">
        <button
          onClick={() => save.mutate(draft)}
          disabled={save.isPending}
          className="px-4 py-2 rounded bg-[var(--accent)] text-[var(--accent-foreground)] text-sm font-medium disabled:opacity-30"
        >
          {save.isPending ? "Saving…" : "Save"}
        </button>
        {save.isSuccess && <span className="text-xs text-[var(--positive)]">Saved.</span>}
        {save.isError && <span className="text-xs text-[var(--negative)]">Save failed.</span>}
      </div>
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value?: string | null; onChange: (v: string) => void }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-[var(--muted-foreground)]">{label}</span>
      <input
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="px-2.5 py-1.5 rounded border border-[var(--border)] bg-transparent text-sm"
      />
    </label>
  );
}

function FieldNum({ label, value, onChange }: { label: string; value?: number | null; onChange: (v: number | null) => void }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-[var(--muted-foreground)]">{label}</span>
      <input
        type="number"
        step="any"
        value={value ?? ""}
        onChange={(e) => {
          const v = e.target.value === "" ? null : parseFloat(e.target.value);
          onChange(Number.isNaN(v) ? null : v);
        }}
        className="px-2.5 py-1.5 rounded border border-[var(--border)] bg-transparent text-sm num"
      />
    </label>
  );
}

function Select({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-[var(--muted-foreground)]">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="px-2.5 py-1.5 rounded border border-[var(--border)] bg-transparent text-sm"
      >
        {options.map((o) => <option key={o} value={o}>{o || "—"}</option>)}
      </select>
    </label>
  );
}
