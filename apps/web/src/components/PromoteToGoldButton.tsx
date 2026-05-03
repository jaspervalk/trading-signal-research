"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, type Call } from "@/lib/api";

/**
 * Promote a real ExtractedCall (its context_text + extracted fields) to the
 * gold set as a positive example. The user can then refine on the gold edit page.
 */
export function PromoteToGoldButton({ call }: { call: Call }) {
  const qc = useQueryClient();
  const router = useRouter();
  const [done, setDone] = useState<number | null>(null);

  const promote = useMutation({
    mutationFn: () =>
      api.gold.create({
        source_key: `call:${call.id}`,
        source_text: call.context_text ?? call.evidence_quote ?? "",
        expected_is_call: true,
        origin_call_id: call.id,
        origin_document_id: call.document_id,
        expected_ticker: call.ticker,
        expected_direction: call.direction,
        expected_entry_type: call.entry_type,
        expected_entry_price: call.entry_price,
        expected_target_price: call.target_price,
        expected_stop_price: call.stop_price,
        expected_timeframe: call.timeframe,
        notes: `Promoted from call #${call.id}`,
      }),
    onSuccess: (g) => {
      qc.invalidateQueries({ queryKey: ["gold"] });
      setDone(g.id);
    },
  });

  if (done) {
    return (
      <button
        onClick={() => router.push(`/gold/${done}`)}
        className="text-xs px-3 py-1.5 rounded border border-[var(--positive)]/30 text-[var(--positive)] hover:bg-[var(--positive)]/10"
      >
        ✓ Added — edit gold label
      </button>
    );
  }

  return (
    <button
      onClick={() => promote.mutate()}
      disabled={promote.isPending}
      className="text-xs px-3 py-1.5 rounded border border-[var(--border)] hover:bg-[var(--muted)] disabled:opacity-30"
      title="Add this call to the extractor evaluation gold set"
    >
      {promote.isPending ? "Adding…" : "+ Promote to gold set"}
    </button>
  );
}
