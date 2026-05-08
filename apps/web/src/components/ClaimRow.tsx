import { Badge } from "@/components/Card";
import type { TickerClaim } from "@/lib/api";
import {
  cn,
  fmtIsoDate,
  fmtVideoTime,
  polarityColor,
  polarityGlyph,
} from "@/lib/utils";

/**
 * One row of the claims feed, with native `<details>` collapse/expand drawer.
 *
 * - Collapsed (default): compact terminal-style row — date · creator · type · class
 *   · polarity · summary · chevron.
 * - Expanded: drawer with the verbatim evidence quote in Newsreader serif italic,
 *   cyan left rule, and a source-timestamp link.
 *
 * No JS state; CSS handles the open/closed visual difference via `[open]` selector
 * on the parent details element.
 */
export function ClaimRow({ claim }: { claim: TickerClaim }) {
  const sourceUrl =
    claim.document_url && claim.context_start_seconds !== null
      ? appendYouTubeTimestamp(claim.document_url, claim.context_start_seconds)
      : claim.document_url;

  return (
    <details className="group border-b border-[var(--hairline-2)] last:border-b-0 font-mono-jb open:bg-[color:rgba(34,211,238,0.03)]">
      <summary className="list-none cursor-pointer hover:bg-[color:rgba(255,255,255,0.02)] grid grid-cols-[92px_minmax(110px,170px)_72px_72px_56px_minmax(0,1fr)_16px] gap-2.5 items-center px-4 py-2.5 text-xs">
        <span className="text-[var(--muted-foreground)]">{fmtIsoDate(claim.posted_at)}</span>
        <span className="truncate">{claim.creator_name}</span>
        <Badge className="text-[var(--muted)] border-[var(--border)] uppercase tracking-wider">
          {claim.claim_type.replace(/_/g, " ")}
        </Badge>
        <Badge className={cn("uppercase tracking-wider", classBadgeColor(claim.claim_class))}>
          {claim.claim_class}
        </Badge>
        <span
          className={cn(
            "inline-flex items-center gap-1 text-xs font-medium",
            polarityColor(claim.polarity),
          )}
        >
          {polarityGlyph(claim.polarity)}{" "}
          <span className="text-[var(--muted-foreground)] font-normal">
            {abbreviatePolarity(claim.polarity)}
          </span>
        </span>
        <span className="truncate text-[var(--foreground)]">{claim.summary}</span>
        <span className="text-[var(--muted-foreground)] text-[11px] transition-transform group-open:rotate-180 group-open:text-[var(--info)]">
          ▾
        </span>
      </summary>
      <div className="bg-[var(--panel-2)] border-t border-[var(--hairline-2)] px-8 py-7 grid gap-3.5">
        <div className="border-l border-[var(--info)] pl-5">
          <p className="font-serif-italic text-[23px] leading-[1.45] text-[#ECECEC]">
            <span className="text-[var(--muted-2)] not-italic">«&nbsp;</span>
            {claim.evidence_quote}
            <span className="text-[var(--muted-2)] not-italic">&nbsp;»</span>
          </p>
        </div>
        <div className="text-[11px] tracking-wide text-[var(--muted-foreground)] flex items-center gap-3 flex-wrap">
          <span>
            {claim.creator_name}
            {claim.document_title ? ` · ${claim.document_title}` : ""}
          </span>
          {sourceUrl ? (
            <a
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--info)] hover:underline"
            >
              Source: video at {fmtVideoTime(claim.context_start_seconds)} →
            </a>
          ) : (
            <span className="text-[var(--muted-2)]">Source unavailable</span>
          )}
          <span className="text-[var(--muted-2)]">
            confidence {claim.final_confidence.toFixed(2)}
          </span>
        </div>
      </div>
    </details>
  );
}

function abbreviatePolarity(polarity: TickerClaim["polarity"]): string {
  if (polarity === "bullish") return "+1";
  if (polarity === "bearish") return "−1";
  if (polarity === "mixed") return "±";
  return "0";
}

function classBadgeColor(claimClass: TickerClaim["claim_class"]): string {
  switch (claimClass) {
    case "factual":
      return "text-[var(--info)] border-[color:rgba(34,211,238,0.4)] bg-[color:rgba(34,211,238,0.06)]";
    case "opinion":
      return "text-[var(--muted-foreground)] border-[var(--border)]";
    case "speculation":
      return "text-[var(--warning)] border-[color:rgba(245,158,11,0.4)] bg-[color:rgba(245,158,11,0.06)]";
    case "hype":
      return "text-[var(--negative)] border-[color:rgba(239,68,68,0.4)] bg-[color:rgba(239,68,68,0.06)]";
  }
}

/** Append `&t={n}s` to a YouTube URL, handling existing query strings. */
function appendYouTubeTimestamp(url: string, seconds: number): string {
  const sec = Math.floor(seconds);
  if (!url.includes("youtube.com") && !url.includes("youtu.be")) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}t=${sec}s`;
}
