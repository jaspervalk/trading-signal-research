"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Checks if (entity_type, entity_id) is on the watchlist; pin/unpin toggles it.
 * Renders an outline button that turns filled-yellow when pinned.
 */
export function WatchlistButton({
  entityType, entityId,
}: { entityType: string; entityId: string }) {
  const qc = useQueryClient();
  const queryKey = ["watchlist", entityType];

  const { data } = useQuery({
    queryKey,
    queryFn: () => api.watchlist.list(entityType),
  });

  const existing = data?.find((w) => w.entity_id === entityId);

  const pin = useMutation({
    mutationFn: () => api.watchlist.pin({ entity_type: entityType, entity_id: entityId }),
    onSuccess: () => qc.invalidateQueries({ queryKey }),
  });

  const unpin = useMutation({
    mutationFn: (id: number) => api.watchlist.unpin(id),
    onSuccess: () => qc.invalidateQueries({ queryKey }),
  });

  return (
    <button
      onClick={() => (existing ? unpin.mutate(existing.id) : pin.mutate())}
      disabled={pin.isPending || unpin.isPending}
      className={cn(
        "px-3 py-1.5 rounded border text-sm transition-colors flex items-center gap-1.5",
        existing
          ? "bg-[var(--warning)]/10 border-[var(--warning)]/30 text-[var(--warning)]"
          : "border-[var(--border)] hover:bg-[var(--muted)]",
      )}
    >
      {existing ? "★ Watching" : "☆ Watch"}
    </button>
  );
}
