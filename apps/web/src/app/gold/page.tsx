"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { Card, CardHeader, Badge } from "@/components/Card";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/utils";

export default function GoldSetPage() {
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["gold"],
    queryFn: () => api.gold.list(),
  });

  const importJsonl = useMutation({
    mutationFn: () => api.gold.importJsonl(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["gold"] }),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.gold.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["gold"] }),
  });

  const positives = (data ?? []).filter((g) => g.expected_is_call);
  const negatives = (data ?? []).filter((g) => !g.expected_is_call);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Gold set</h1>
          <p className="text-sm text-[var(--muted-foreground)] mt-1 max-w-2xl">
            Hand-labeled extraction examples. The extractor is evaluated against this set every time it changes — precision/recall on these is the most important number we track.
          </p>
        </div>
        <button
          onClick={() => importJsonl.mutate()}
          disabled={importJsonl.isPending}
          className="px-3 py-1.5 rounded border border-[var(--border)] text-sm hover:bg-[var(--muted)] disabled:opacity-30"
        >
          {importJsonl.isPending ? "Importing…" : "Import from JSONL"}
        </button>
      </div>

      {importJsonl.data && (
        <div className="text-sm text-[var(--positive)]">
          Imported: {importJsonl.data.inserted} new, {importJsonl.data.updated} updated.
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 text-sm">
        <Card>
          <div className="text-2xl font-semibold num">{positives.length}</div>
          <div className="text-xs text-[var(--muted-foreground)] uppercase tracking-wider mt-1">Positive examples</div>
        </Card>
        <Card>
          <div className="text-2xl font-semibold num">{negatives.length}</div>
          <div className="text-xs text-[var(--muted-foreground)] uppercase tracking-wider mt-1">Negative examples</div>
        </Card>
      </div>

      <Card>
        <CardHeader title="Labels" subtitle="Click an entry to edit. Promote a real call from its detail page." />
        {isLoading && <div className="text-sm text-[var(--muted-foreground)]">Loading…</div>}
        {data && data.length === 0 && (
          <p className="text-sm text-[var(--muted-foreground)]">
            Empty. Click <em>Import from JSONL</em> to seed from <code>data/gold/extraction_gold.jsonl</code>, or promote real calls from their detail pages.
          </p>
        )}
        {data && data.length > 0 && (
          <ul className="divide-y divide-[var(--border)] -my-2">
            {data.map((g) => (
              <li key={g.id} className="py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <Link href={`/gold/${g.id}`} className="font-mono text-sm font-medium hover:underline">
                        {g.source_key}
                      </Link>
                      {g.expected_is_call ? (
                        <Badge className="bg-[var(--positive)]/10 text-[var(--positive)] border-[var(--positive)]/20">
                          {g.expected_ticker} {g.expected_direction}
                        </Badge>
                      ) : (
                        <Badge className="bg-[var(--muted)] text-[var(--muted-foreground)] border-[var(--border)]">
                          no-call
                        </Badge>
                      )}
                    </div>
                    <p className="text-sm text-[var(--muted-foreground)] line-clamp-2">{g.source_text}</p>
                    {g.notes && <p className="text-xs text-[var(--muted-foreground)] mt-1 italic">{g.notes}</p>}
                  </div>
                  <div className="flex flex-col items-end gap-1 text-xs text-[var(--muted-foreground)]">
                    <span>{fmtDate(g.created_at)}</span>
                    <button
                      onClick={() => remove.mutate(g.id)}
                      className="hover:text-[var(--negative)]"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
