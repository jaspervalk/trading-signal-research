"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const items = [
  { href: "/", label: "Leaderboard" },
  { href: "/calls", label: "Calls" },
  { href: "/tickers", label: "Tickers" },
  { href: "/watchlist", label: "Watchlist" },
  { href: "/gold", label: "Gold set" },
  { href: "/methodology", label: "Methodology" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="border-b border-[var(--border)]">
      <div className="max-w-7xl mx-auto px-6 h-14 flex items-center gap-2">
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
      </div>
    </nav>
  );
}
