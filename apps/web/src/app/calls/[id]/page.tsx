"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";

import { Card, CardHeader, Badge } from "@/components/Card";
import { ReviewActions } from "@/components/ReviewActions";
import { Annotations } from "@/components/Annotations";
import { Tags } from "@/components/Tags";
import { WatchlistButton } from "@/components/WatchlistButton";
import { PromoteToGoldButton } from "@/components/PromoteToGoldButton";
import { api } from "@/lib/api";
import { cn, fmtDateTime, fmtPct, pctColor, statusColor } from "@/lib/utils";

export default function CallDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const callId = parseInt(id, 10);

  const { data, isLoading, error } = useQuery({
    queryKey: ["call", callId],
    queryFn: () => api.calls.get(callId),
  });

  if (isLoading) return <div className="text-sm text-[var(--muted-foreground)]">Loading…</div>;
  if (error || !data) return <div className="text-sm text-[var(--negative)]">Not found.</div>;

  const c = data.call;
  const youtubeUrl =
    data.document_url && c.context_start_seconds
      ? `${data.document_url}&t=${Math.floor(c.context_start_seconds)}s`
      : data.document_url;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <Link href="/calls" className="text-sm text-[var(--muted-foreground)] hover:underline">← Calls</Link>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-3">
            <span className="font-mono">{c.ticker}</span>
            <span className="text-base font-normal text-[var(--muted-foreground)]">
              {c.direction} · {c.entry_type}
              {c.entry_price ? ` @ ${c.entry_price}` : ""}
            </span>
          </h1>
          <div className="flex items-center gap-2 mt-2 text-sm text-[var(--muted-foreground)]">
            <Link href={`/creators/${data.creator_id}`} className="hover:underline">{data.creator_name}</Link>
            <span>·</span>
            <span>{fmtDateTime(data.posted_at)}</span>
            <span>·</span>
            {youtubeUrl && (
              <a href={youtubeUrl} target="_blank" rel="noopener noreferrer" className="hover:underline">
                source video ↗
              </a>
            )}
          </div>
        </div>
        <WatchlistButton entityType="call" entityId={String(c.id)} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <Card>
            <CardHeader title="Extracted call" />
            <dl className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-sm">
              <Row label="Ticker" value={<span className="font-mono">{c.ticker}</span>} />
              <Row label="Direction" value={c.direction} />
              <Row label="Entry type" value={c.entry_type} />
              <Row label="Entry price" value={c.entry_price ?? "—"} />
              <Row label="Target" value={c.target_price ?? "—"} />
              <Row label="Stop" value={c.stop_price ?? "—"} />
              <Row label="Timeframe" value={c.timeframe} />
              <Row label="Final confidence" value={c.final_confidence.toFixed(2)} />
              <Row label="Pipeline status" value={<Badge className={statusColor(c.status)}>{c.status}</Badge>} />
              <Row label="Manual status" value={<Badge className={statusColor(c.manual_status)}>{c.manual_status}</Badge>} />
              <Row label="LLM conf." value={c.llm_confidence?.toFixed(2) ?? "—"} />
              <Row label="Rule conf." value={c.rule_confidence?.toFixed(2) ?? "—"} />
            </dl>
          </Card>

          {c.reasoning_summary && (
            <Card>
              <CardHeader title="Speaker reasoning (LLM summary)" />
              <p className="text-sm leading-relaxed">{c.reasoning_summary}</p>
            </Card>
          )}

          {c.evidence_quote && (
            <Card>
              <CardHeader title="Evidence quote" subtitle="Verbatim from the source — verified to substring-match" />
              <blockquote className="border-l-2 border-[var(--accent)] pl-4 italic text-sm">
                &ldquo;{c.evidence_quote}&rdquo;
              </blockquote>
            </Card>
          )}

          {c.context_text && (
            <Card>
              <CardHeader
                title="Source context"
                subtitle={`±90s window — ${c.context_start_seconds?.toFixed(0)}s to ${c.context_end_seconds?.toFixed(0)}s`}
              />
              <div className="text-sm leading-relaxed whitespace-pre-wrap text-[var(--muted-foreground)] max-h-96 overflow-y-auto">
                {c.context_text}
              </div>
            </Card>
          )}

          <Card>
            <CardHeader title="Outcomes" />
            {data.outcomes.length === 0 ? (
              <p className="text-sm text-[var(--muted-foreground)]">No outcomes yet — call posted too recently for the trigger window to have closed.</p>
            ) : (
              <table className="w-full text-sm num">
                <thead>
                  <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wider text-[var(--muted-foreground)]">
                    <th className="py-2">Horizon</th>
                    <th className="py-2 text-right">Activated</th>
                    <th className="py-2 text-right">Fill</th>
                    <th className="py-2 text-right">Return</th>
                    <th className="py-2 text-right">vs SPY</th>
                    <th className="py-2 text-right">MFE</th>
                    <th className="py-2 text-right">MAE</th>
                    <th className="py-2">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.outcomes.map((o) => (
                    <tr key={o.id} className="border-b border-[var(--border)] last:border-0">
                      <td className="py-2 font-medium">{o.horizon}</td>
                      <td className="py-2 text-right">{o.activated ? "✓" : "—"}</td>
                      <td className="py-2 text-right">{o.entry_fill_price?.toFixed(2) ?? "—"}</td>
                      <td className={cn("py-2 text-right", pctColor(o.return_pct))}>{fmtPct(o.return_pct)}</td>
                      <td className={cn("py-2 text-right", pctColor(o.excess_return_pct))}>{fmtPct(o.excess_return_pct)}</td>
                      <td className="py-2 text-right text-[var(--muted-foreground)]">{fmtPct(o.mfe)}</td>
                      <td className="py-2 text-right text-[var(--muted-foreground)]">{fmtPct(o.mae)}</td>
                      <td className="py-2"><Badge className={statusColor(o.status)}>{o.status}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Review" />
            <ReviewActions call={c} />
          </Card>

          <Card>
            <CardHeader title="Tags" />
            <Tags entityType="call" entityId={String(c.id)} />
          </Card>

          <Card>
            <CardHeader title="Notes" />
            <Annotations entityType="call" entityId={String(c.id)} />
          </Card>

          <Card>
            <CardHeader title="Gold set" subtitle="Use this call as an extractor test case." />
            <PromoteToGoldButton call={c} />
          </Card>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wider text-[var(--muted-foreground)]">{label}</dt>
      <dd className="mt-0.5">{value}</dd>
    </div>
  );
}
