"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function TickerSearch() {
  const router = useRouter();
  const [value, setValue] = useState("");

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const t = value.trim().toUpperCase();
        if (t) router.push(`/tickers/${t}`);
      }}
      className="flex gap-2"
    >
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Research a ticker — e.g. NVDA"
        className="flex-1 rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm"
        aria-label="Research a ticker"
      />
      <button
        type="submit"
        className="rounded-md border border-[var(--border)] px-4 py-2 text-sm font-medium"
      >
        Research →
      </button>
    </form>
  );
}
