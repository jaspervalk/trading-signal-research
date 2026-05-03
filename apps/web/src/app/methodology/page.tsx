import { Card, CardHeader } from "@/components/Card";

export default function MethodologyPage() {
  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Methodology</h1>
        <p className="text-sm text-[var(--muted-foreground)] mt-1">
          How the pipeline works, what assumptions it makes, and where to find the canonical design docs.
        </p>
      </div>

      <Card>
        <CardHeader title="Pipeline" />
        <ol className="list-decimal pl-5 space-y-2 text-sm leading-relaxed">
          <li>
            <strong>Ingest</strong> trader content from YouTube. Source-agnostic adapter so Discord plugs in later. Whisper fallback for IP-blocked / missing transcripts.
          </li>
          <li>
            <strong>Extract</strong> structured trade calls via a hybrid pipeline:
            rule-based prefilter → Claude tool-use with strict JSON schema and required <code>evidence_quote</code> per non-null field → rule validator that rejects unsupported fields.
          </li>
          <li>
            <strong>Backtest</strong> each call deterministically. Activation is separated from outcome. Time-aware: only bars with <code>timestamp ≥ posted_at</code> can drive any decision. Costs applied as a fixed 10 bps round-trip.
          </li>
          <li>
            <strong>Score</strong> creators with multi-metric scorecards. Wilson 95% CIs on hit rates. Excess vs SPY as the headline. <strong>N is shown alongside every rate</strong> — small N collapses everything to noise.
          </li>
        </ol>
      </Card>

      <Card>
        <CardHeader title="What this is not" />
        <ul className="list-disc pl-5 space-y-1.5 text-sm text-[var(--muted-foreground)]">
          <li>Not a trading bot. Nothing executes orders.</li>
          <li>Not investment advice. See the disclaimer.</li>
          <li>Not a final result yet — current N per creator is too small to draw conclusions. The point is to build the harness, run it for months, and report what the data actually shows.</li>
        </ul>
      </Card>

      <Card>
        <CardHeader title="Conventions" />
        <ul className="list-disc pl-5 space-y-1.5 text-sm">
          <li>Datetimes are stored UTC. Market math runs in <code>America/New_York</code>.</li>
          <li>Trading-day horizons (1d / 3d / 5d / 21d), not calendar days.</li>
          <li>Activation rules per <code>entry_type</code> are documented in ADR 0003.</li>
          <li>Hit rates &lt; 5 activated calls are flagged as <em>low N</em> in the leaderboard.</li>
        </ul>
      </Card>

      <Card>
        <CardHeader title="Decision records" subtitle="Read these before trusting a number." />
        <ul className="text-sm space-y-2">
          <li>
            <a href="https://github.com/jaspervalk/trading-signal-research/blob/main/docs/architecture.md" target="_blank" rel="noopener noreferrer" className="text-[var(--accent)] hover:underline">
              Architecture overview ↗
            </a>
            <span className="text-[var(--muted-foreground)] ml-2">— full system design with diagram and locked decisions</span>
          </li>
          <li>
            <a href="https://github.com/jaspervalk/trading-signal-research/blob/main/docs/decisions/0001-source-abstraction.md" target="_blank" rel="noopener noreferrer" className="text-[var(--accent)] hover:underline">
              ADR 0001 — Source abstraction ↗
            </a>
            <span className="text-[var(--muted-foreground)] ml-2">— how Discord plugs in later</span>
          </li>
          <li>
            <a href="https://github.com/jaspervalk/trading-signal-research/blob/main/docs/decisions/0002-extraction-hybrid.md" target="_blank" rel="noopener noreferrer" className="text-[var(--accent)] hover:underline">
              ADR 0002 — Hybrid call extraction ↗
            </a>
            <span className="text-[var(--muted-foreground)] ml-2">— rules + LLM + validator + gold set</span>
          </li>
          <li>
            <a href="https://github.com/jaspervalk/trading-signal-research/blob/main/docs/decisions/0003-backtest-assumptions.md" target="_blank" rel="noopener noreferrer" className="text-[var(--accent)] hover:underline">
              ADR 0003 — Backtest assumptions ↗
            </a>
            <span className="text-[var(--muted-foreground)] ml-2">— activation rules, fill assumptions, costs, leakage controls</span>
          </li>
          <li>
            <a href="https://github.com/jaspervalk/trading-signal-research/blob/main/docs/decisions/0004-transcript-ingestion.md" target="_blank" rel="noopener noreferrer" className="text-[var(--accent)] hover:underline">
              ADR 0004 — Transcript ingestion ↗
            </a>
            <span className="text-[var(--muted-foreground)] ml-2">— per-fetch delay + Whisper fallback for IP blocks</span>
          </li>
          <li>
            <a href="https://github.com/jaspervalk/trading-signal-research/blob/main/docs/disclaimer.md" target="_blank" rel="noopener noreferrer" className="text-[var(--accent)] hover:underline">
              Disclaimer ↗
            </a>
          </li>
        </ul>
      </Card>

      <Card>
        <CardHeader title="Source code" />
        <p className="text-sm">
          <a href="https://github.com/jaspervalk/trading-signal-research" target="_blank" rel="noopener noreferrer" className="text-[var(--accent)] hover:underline">
            github.com/jaspervalk/trading-signal-research ↗
          </a>
        </p>
      </Card>
    </div>
  );
}
