"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

import { cn } from "@/lib/utils";

const items = [
  { href: "/", label: "Portfolio" },
  { href: "/tickers", label: "Research" },
  { href: "/watchlist", label: "Watchlist" },
  { href: "/creators", label: "Creators" },
  { href: "/methodology", label: "Methodology" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="border-b border-[var(--border)]">
      <div className="max-w-[1480px] mx-auto px-6 h-14 flex items-center gap-2">
        <Link href="/" className="font-semibold tracking-tight mr-6">
          trading-signal-research
        </Link>
        <div className="flex items-center gap-1 text-sm">
          {items.map((it) => {
            const active =
              it.href === "/"
                ? pathname === "/"
                : pathname.startsWith(it.href);
            return (
              <Link
                key={it.href}
                href={it.href}
                className={cn(
                  "px-3 py-1.5 rounded-md transition-colors",
                  active
                    ? "bg-[var(--muted)] text-[var(--foreground)]"
                    : "text-[var(--muted-foreground)] hover:text-[var(--foreground)] hover:bg-[var(--muted)]",
                )}
              >
                {it.label}
              </Link>
            );
          })}
        </div>
        <div className="flex-1" />
        <TickerSearch />
      </div>
    </nav>
  );
}

/**
 * "Look up any ticker" input. Submits to /tickers/{TICKER}; works for any symbol
 * yfinance can resolve, regardless of whether it appears in the tracked universe
 * or has any extracted calls/claims.
 */
function TickerSearch() {
  const router = useRouter();
  const [value, setValue] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const cleaned = value.trim().toUpperCase();
    if (!cleaned) return;
    // Allow alphanum, dot, hyphen (for tickers like BRK.B or RDS-A); strip anything else.
    const safe = cleaned.replace(/[^A-Z0-9.\-]/g, "");
    if (!safe) return;
    router.push(`/tickers/${safe}`);
    setValue("");
  }

  return (
    <form onSubmit={submit} className="flex items-center gap-2">
      <span className="text-[11px] uppercase tracking-wider text-[var(--muted-foreground)] font-mono-jb">
        look up
      </span>
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="TICKER"
        className="font-mono-jb text-sm w-32 bg-[var(--panel)] border border-[var(--border)] px-2 py-1 placeholder:text-[var(--muted-2)] focus:outline-none focus:border-[var(--info)] focus:ring-0 uppercase"
        spellCheck={false}
        autoComplete="off"
      />
    </form>
  );
}
