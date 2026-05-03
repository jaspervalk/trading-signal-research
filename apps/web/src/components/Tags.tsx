"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";

const SUGGESTED = ["watch", "false_positive", "gold_example", "investigate", "golden_call"];

export function Tags({
  entityType, entityId,
}: { entityType: string; entityId: string }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");

  const queryKey = ["tags", entityType, entityId];
  const { data } = useQuery({
    queryKey,
    queryFn: () => api.tags.list(entityType, entityId),
  });

  const create = useMutation({
    mutationFn: (label: string) => api.tags.create({ entity_type: entityType, entity_id: entityId, label }),
    onSuccess: () => { qc.invalidateQueries({ queryKey }); setDraft(""); },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.tags.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey }); },
  });

  const existing = new Set((data ?? []).map((t) => t.label));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {(data ?? []).map((t) => (
          <button
            key={t.id}
            onClick={() => remove.mutate(t.id)}
            className="text-xs px-2 py-0.5 rounded bg-[var(--muted)] hover:bg-[var(--negative)]/10 hover:text-[var(--negative)] border border-[var(--border)]"
            title="Click to remove"
          >
            {t.label} ×
          </button>
        ))}
        {(!data || data.length === 0) && (
          <p className="text-xs text-[var(--muted-foreground)]">No tags.</p>
        )}
      </div>

      <div className="flex gap-1">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && draft.trim() && !existing.has(draft.trim())) {
              create.mutate(draft.trim());
            }
          }}
          placeholder="add tag…"
          className="flex-1 px-2 py-1 rounded border border-[var(--border)] bg-transparent text-xs"
        />
      </div>

      <div className="flex flex-wrap gap-1">
        {SUGGESTED.filter((s) => !existing.has(s)).map((s) => (
          <button
            key={s}
            onClick={() => create.mutate(s)}
            className="text-xs px-2 py-0.5 rounded border border-dashed border-[var(--border)] text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
          >
            + {s}
          </button>
        ))}
      </div>
    </div>
  );
}
