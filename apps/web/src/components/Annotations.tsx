"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/utils";

export function Annotations({
  entityType, entityId,
}: { entityType: string; entityId: string }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");

  const queryKey = ["annotations", entityType, entityId];
  const { data } = useQuery({
    queryKey,
    queryFn: () => api.annotations.list(entityType, entityId),
  });

  const create = useMutation({
    mutationFn: () => api.annotations.create({ entity_type: entityType, entity_id: entityId, body: draft }),
    onSuccess: () => { qc.invalidateQueries({ queryKey }); setDraft(""); },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.annotations.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey }); },
  });

  return (
    <div className="space-y-3">
      <div>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={2}
          placeholder="Add a note…"
          className="w-full px-2.5 py-1.5 rounded border border-[var(--border)] bg-transparent text-sm"
        />
        <button
          onClick={() => draft.trim() && create.mutate()}
          disabled={!draft.trim() || create.isPending}
          className="mt-2 px-3 py-1.5 text-xs rounded border border-[var(--border)] hover:bg-[var(--muted)] disabled:opacity-30"
        >
          Save note
        </button>
      </div>

      <div className="space-y-2">
        {(data ?? []).map((a) => (
          <div key={a.id} className="rounded border border-[var(--border)] p-3 text-sm">
            <p className="whitespace-pre-wrap">{a.body}</p>
            <div className="flex items-center justify-between mt-2 text-xs text-[var(--muted-foreground)]">
              <span>{fmtDateTime(a.created_at)}</span>
              <button onClick={() => remove.mutate(a.id)} className="hover:text-[var(--negative)]">
                Delete
              </button>
            </div>
          </div>
        ))}
        {(!data || data.length === 0) && (
          <p className="text-xs text-[var(--muted-foreground)]">No notes yet.</p>
        )}
      </div>
    </div>
  );
}
