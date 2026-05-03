"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Call } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUSES = [
  { value: "unreviewed", label: "Unreviewed" },
  { value: "confirmed", label: "Confirmed" },
  { value: "rejected", label: "Rejected" },
  { value: "flagged", label: "Flagged" },
];

export function ReviewActions({ call }: { call: Call }) {
  const qc = useQueryClient();
  const [notes, setNotes] = useState(call.manual_notes ?? "");

  const mutate = useMutation({
    mutationFn: (vars: { manual_status: string; manual_notes?: string }) =>
      api.review.setStatus(call.id, vars.manual_status, vars.manual_notes),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["call", call.id] });
      qc.invalidateQueries({ queryKey: ["calls"] });
    },
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1">
        {STATUSES.map((s) => (
          <button
            key={s.value}
            disabled={mutate.isPending}
            onClick={() => mutate.mutate({ manual_status: s.value, manual_notes: notes })}
            className={cn(
              "px-3 py-1.5 rounded text-sm border transition-colors",
              call.manual_status === s.value
                ? "bg-[var(--accent)] text-[var(--accent-foreground)] border-[var(--accent)]"
                : "border-[var(--border)] hover:bg-[var(--muted)]",
            )}
          >
            {s.label}
          </button>
        ))}
      </div>

      <div>
        <label className="text-xs text-[var(--muted-foreground)] block mb-1">Review notes</label>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() =>
            notes !== (call.manual_notes ?? "") &&
            mutate.mutate({ manual_status: call.manual_status, manual_notes: notes })
          }
          rows={3}
          className="w-full px-2.5 py-1.5 rounded border border-[var(--border)] bg-transparent text-sm"
          placeholder="Why this status? Save on blur."
        />
      </div>

      {mutate.isError && <div className="text-xs text-[var(--negative)]">Save failed. Retry?</div>}
    </div>
  );
}
