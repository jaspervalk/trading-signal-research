/**
 * Where a panel's contents came from.
 *
 * The most important thing this interface has to communicate is which numbers
 * are arithmetic over price bars and which are a language model's prose. They
 * carry different weight and different failure modes, and once they sit in
 * identical panels a reader has to remember which is which. A quiet, uniform
 * tag in the panel header removes the guessing without shouting.
 *
 * Deliberately not colour-coded: provenance is a category, not a severity, and
 * colour here is reserved for direction and risk.
 */
export function Provenance({
  kind,
  detail,
}: {
  kind: "computed" | "model" | "manual";
  /** Optional qualifier, e.g. a cost or a freshness stamp. */
  detail?: string;
}) {
  return (
    <span className="text-[10px] uppercase tracking-[0.14em] text-[var(--muted-2)] shrink-0">
      {LABEL[kind]}
      {detail ? ` · ${detail}` : ""}
    </span>
  );
}

const LABEL: Record<"computed" | "model" | "manual", string> = {
  computed: "computed",
  model: "model-written",
  manual: "yours",
};
