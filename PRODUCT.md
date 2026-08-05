# PRODUCT.md

Design context for `trading-signal-research`. Written 2026-08-05 from the shipped
code and ADRs 0005 / 0008 / 0009, not from an interview.

## Register

**product** — this is a tool the owner is *inside*, doing a task. Design serves
the work. Consistency beats surprise; the interface should disappear into the
decision. No marketing surfaces exist in this app.

## Users

One user: Jasper, an AI engineer who invests his own money through Revolut.
Quant-literate (BSc AI, comfortable with pandas, Wilson intervals, walk-forward
validation). Not a day trader. Uses this a few times a week, at a desk, on a
wide monitor, deliberately rather than urgently: "is this position still worth
holding, and if I add, where."

He is the only user. There is no onboarding to design for, no permissions
model, no multi-tenancy. Density is a feature; he would rather see the number
than a friendly summary of the number.

## Product purpose

Decision support for ticker selection and position management. Two sections
(ADR 0009):

1. **Analysis** — screener, technicals, fundamentals, entry/exit research
   (deterministic levels + optional LLM panel), walk-forward backtests.
2. **Portfolio Manager** — a manual trade ledger; the system of record for what
   he actually holds.

The YouTube-creator pipeline is a **side feature**: one signal source among
several, deliberately demoted in 2026-08. It must not dominate any surface.

## Strategic principles

These are load-bearing and visible in the UI, not just the code.

- **Computed and generated must be distinguishable at a glance.** Arithmetic on
  price bars, LLM prose, and derived labels are three different epistemic
  categories. A reader must never have to guess which one they are looking at.
  This is the single most important design constraint in the product.
- **Absence is shown, never implied.** A missing multiple renders as "—" with
  the field named, not as a blank or a zero. Totals that would mislead render
  as "—" rather than a partial sum.
- **Evidence travels with claims.** Sample sizes, Wilson intervals, source
  quotes, `as of` stamps. A number without its provenance is incomplete.
- **No composite scores.** Never collapse independent signals into one number.
  Show the components.
- **The user pulls the trigger.** The system ranks, explains, and surfaces
  risk. It never says buy, never sizes a position, never places an order.
  `SELL` and `SHORT` do not exist as labels.

## Tone

Instrument panel, not advisor. Terse, technical, quantitative. Lowercase
metric labels, uppercase micro-labels for structure. State findings flatly;
negative results get the same weight as positive ones. No exclamation, no
encouragement, no "great job." The disclaimer is present but never shouty.

## Anti-references

- **Robinhood / consumer trading apps.** Confetti, gamification, big green
  numbers designed to feel good. This product must feel like a measurement
  device.
- **Bloomberg terminal pastiche.** Amber-on-black nostalgia, gratuitous
  density as costume. Density here must be earned by real information.
- **SaaS dashboard template.** Hero metric + sparkline + three stat cards +
  gradient accent. Also: identical card grids where every panel is the same
  size regardless of importance.
- **AI-product chrome.** Sparkle icons, "✨ AI insights", purple gradients,
  chat bubbles. The LLM panels here are a costed, auditable tool, not magic.

## Established visual system

Do not redesign these; they are consistent across the app and changing one
page's vocabulary would be a regression.

- Dark surface: `--background #0a0a0a`, panels `--panel #141414`, borders
  `--border #262626`, hairlines `--hairline-2 #1f1f1f`.
- Semantic color only: `--positive`, `--negative`, `--warning`, `--info`.
  Color carries meaning; it is never decoration.
- JetBrains Mono (`.font-mono-jb`) for all data and metric labels; system sans
  for prose; Newsreader italic sparingly for editorial asides.
- Square panels (no border radius), `px-4 py-3` headers with an 11px uppercase
  tracked sub-label, hairline dividers.
