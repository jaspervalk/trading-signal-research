"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";

const FIELD =
  "rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm";

export function AddTradeForm() {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    ticker: "",
    side: "buy" as "buy" | "sell",
    quantity: "",
    price_per_share: "",
    traded_at: new Date().toISOString().slice(0, 10),
    fees: "",
    eur_amount: "",
    note: "",
  });

  const mutation = useMutation({
    mutationFn: () =>
      api.portfolio.addTrade({
        ticker: form.ticker.trim().toUpperCase(),
        side: form.side,
        quantity: Number(form.quantity),
        price_per_share: Number(form.price_per_share),
        traded_at: new Date(`${form.traded_at}T00:00:00Z`).toISOString(),
        fees: form.fees ? Number(form.fees) : 0,
        eur_amount: form.eur_amount ? Number(form.eur_amount) : null,
        note: form.note || null,
      }),
    onSuccess: () => {
      setError(null);
      setForm({ ...form, ticker: "", quantity: "", price_per_share: "", fees: "", eur_amount: "", note: "" });
      qc.invalidateQueries({ queryKey: ["portfolio"] });
      qc.invalidateQueries({ queryKey: ["portfolio-trades"] });
    },
    onError: (e: unknown) =>
      setError(e instanceof ApiError ? e.message : "Could not save the trade."),
  });

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        mutation.mutate();
      }}
      className="grid gap-3 sm:grid-cols-4"
    >
      <input required value={form.ticker} onChange={set("ticker")} placeholder="Ticker" className={FIELD} aria-label="Ticker" />
      <select value={form.side} onChange={set("side")} className={FIELD} aria-label="Side">
        <option value="buy">Buy</option>
        <option value="sell">Sell</option>
      </select>
      <input required type="number" step="any" min="0" value={form.quantity} onChange={set("quantity")} placeholder="Quantity" className={FIELD} aria-label="Quantity" />
      <input required type="number" step="any" min="0" value={form.price_per_share} onChange={set("price_per_share")} placeholder="Price per share" className={FIELD} aria-label="Price per share" />
      <input required type="date" value={form.traded_at} onChange={set("traded_at")} className={FIELD} aria-label="Trade date" />
      <input type="number" step="any" min="0" value={form.fees} onChange={set("fees")} placeholder="Fees (optional)" className={FIELD} aria-label="Fees" />
      <input type="number" step="any" min="0" value={form.eur_amount} onChange={set("eur_amount")} placeholder="EUR total (optional)" className={FIELD} aria-label="EUR total" />
      <input value={form.note} onChange={set("note")} placeholder="Note (optional)" className={FIELD} aria-label="Note" />

      <div className="sm:col-span-4 flex items-center gap-3">
        <button
          type="submit"
          disabled={mutation.isPending}
          className="rounded-md border border-[var(--border)] px-4 py-2 text-sm font-medium disabled:opacity-50"
        >
          {mutation.isPending ? "Saving…" : "Add trade"}
        </button>
        {error && <span className="text-sm text-red-500">{error}</span>}
      </div>
    </form>
  );
}
