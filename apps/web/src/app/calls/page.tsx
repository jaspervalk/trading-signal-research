"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Card, CardHeader, Badge } from "@/components/Card";
import { api } from "@/lib/api";
import {
  cn, fmtDate, fmtPct, pctColor, statusColor,
} from "@/lib/utils";

const PAGE_SIZE = 25;

export default function CallsPage() {
  const [ticker, setTicker] = useState("");
  const [direction, setDirection] = useState("");
  const [status, setStatus] = useState("");
  const [manualStatus, setManualStatus] = useState("");
  const [minConf, setMinConf] = useState(0);
  const [offset, setOffset] = useState(0);

  const { data, isLoading, error } = useQuery({
    queryKey: ["calls", { ticker, direction, status, manualStatus, minConf, offset }],
    queryFn: () =>
      api.calls.list({
        ticker: ticker || undefined,
        direction: direction || undefined,
        status: status || undefined,
        manual_status: manualStatus || undefined,
        min_confidence: minConf > 0 ? minConf : undefined,
        limit: PAGE_SIZE,
        offset,
      }),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Calls explorer</h1>
        <p className="text-sm text-[var(--muted-foreground)] mt-1">
          Every extracted trade call. Filter, click in, and review.
        </p>
      </div>

      <Card>
        <CardHeader title="Filters" />
        <div className="flex flex-wrap gap-4 text-sm">
          <Input label="Ticker" value={ticker} onChange={(v) => { setTicker(v.toUpperCase()); setOffset(0); }} placeholder="NVDA" />
          <Select label="Direction" value={direction} onChange={(v) => { setDirection(v); setOffset(0); }}
            options={[{ v: "", l: "any" }, { v: "long", l: "long" }, { v: "short", l: "short" }, { v: "unspecified", l: "unspecified" }]} />
          <Select label="Status" value={status} onChange={(v) => { setStatus(v); setOffset(0); }}
            options={[{ v: "", l: "any" }, { v: "accepted", l: "accepted" }, { v: "pending_review", l: "pending review" }, { v: "rejected", l: "rejected" }]} />
          <Select label="Review" value={manualStatus} onChange={(v) => { setManualStatus(v); setOffset(0); }}
            options={[{ v: "", l: "any" }, { v: "unreviewed", l: "unreviewed" }, { v: "confirmed", l: "confirmed" }, { v: "rejected", l: "rejected" }, { v: "flagged", l: "flagged" }]} />
          <Input label="Min confidence" value={minConf > 0 ? String(minConf) : ""} onChange={(v) => { setMinConf(parseFloat(v) || 0); setOffset(0); }} placeholder="0.7" />
        </div>
      </Card>

      <Card>
        {isLoading && <div className="text-sm text-[var(--muted-foreground)]">Loading…</div>}
        {error && (
          <div className="text-sm text-[var(--negative)]">
            API not reachable. Run <code className="font-mono">uvicorn apps.api.app.main:app --port 8001</code>.
          </div>
        )}
        {data && items.length === 0 && (
          <div className="text-sm text-[var(--muted-foreground)]">No calls match these filters.</div>
        )}
        {items.length > 0 && (
          <>
            <div className="text-xs text-[var(--muted-foreground)] mb-3 num">
              Showing {offset + 1}–{Math.min(offset + items.length, total)} of {total}
            </div>
            <div className="overflow-x-auto -mx-6">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wider text-[var(--muted-foreground)]">
                    <Th>Posted</Th>
                    <Th>Creator</Th>
                    <Th>Ticker</Th>
                    <Th>Dir</Th>
                    <Th>Entry</Th>
                    <Th right>Conf</Th>
                    <Th>Status</Th>
                    <Th>Review</Th>
                    <Th right>5d return</Th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => {
                    const c = it.call;
                    const five = it.outcomes.find((o) => o.horizon === "5d");
                    return (
                      <tr key={c.id} className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--muted)] transition-colors">
                        <Td className="text-[var(--muted-foreground)] num">{fmtDate(it.posted_at)}</Td>
                        <Td>{it.creator_name || "—"}</Td>
                        <Td>
                          <Link href={`/calls/${c.id}`} className="font-medium hover:underline">
                            {c.ticker}
                          </Link>
                        </Td>
                        <Td>{c.direction}</Td>
                        <Td className="text-[var(--muted-foreground)]">
                          {c.entry_type}{c.entry_price ? ` @ ${c.entry_price}` : ""}
                        </Td>
                        <Td right className="num">{c.final_confidence.toFixed(2)}</Td>
                        <Td>
                          <Badge className={statusColor(c.status)}>{c.status}</Badge>
                        </Td>
                        <Td>
                          <Badge className={statusColor(c.manual_status)}>{c.manual_status}</Badge>
                        </Td>
                        <Td right className={cn("num", pctColor(five?.return_pct))}>
                          {five ? fmtPct(five.return_pct) : "—"}
                        </Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between mt-4 text-sm">
              <button
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={offset === 0}
                className="px-3 py-1.5 rounded border border-[var(--border)] disabled:opacity-30"
              >
                ← Prev
              </button>
              <button
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={offset + items.length >= total}
                className="px-3 py-1.5 rounded border border-[var(--border)] disabled:opacity-30"
              >
                Next →
              </button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

function Input({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-[var(--muted-foreground)]">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="px-2.5 py-1.5 rounded border border-[var(--border)] bg-transparent text-sm w-32"
      />
    </label>
  );
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { v: string; l: string }[] }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-[var(--muted-foreground)]">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="px-2.5 py-1.5 rounded border border-[var(--border)] bg-transparent text-sm"
      >
        {options.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
      </select>
    </label>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={cn("py-2 px-4", right && "text-right")}>{children}</th>;
}

function Td({ children, right, className }: { children: React.ReactNode; right?: boolean; className?: string }) {
  return <td className={cn("py-3 px-4", right && "text-right", className)}>{children}</td>;
}
