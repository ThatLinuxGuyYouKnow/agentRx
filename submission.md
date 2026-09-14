# agentRx — Devpost Submission (CALL-E: Most Practical Use Case)

> agentRx calls every pharmacy near you so nobody has to — live stock and
> cash-price checks with one approval.

## Inspiration

Every month, coordinators at senior centers, clinics, and mutual-aid groups
burn hours on the same chore: calling pharmacy after pharmacy to ask *"do you
have this in stock, what's the cash price, when can we pick it up?"* Miss that
chore and seniors miss refills. We wanted to show what CALL-E is actually
for — not robocalls, but goal-driven phone work: an agent that does the
calling nobody has time for, and returns structured answers a human can act on.

## What it does

agentRx is a refill coordinator. Type a drug once (`atorvastatin 20mg x30`),
pick a radius, approve the pharmacy list — **and only then** does it place real
CALL-E calls to all of them in parallel. Each call asks for availability, cash
price, and pickup time, and fills a result schema (`in_stock`,
`cash_price_usd`, `pickup_time`). You get back:

- a cheapest-first comparison table with per-call transcripts,
- a plain-language verdict (*"Good news! Demo Pharmacy has paracetamol in
  stock at $46.39"*) plus a follow-up prompt with tappable `tel:` links,
- refill reminders, an email summary, and a shortage board that watches drugs
  over time and flags stale checks for re-call.

## How we built it

Finder → consent gate → Caller (fan-out, 3 parallel) → Aggregator.

- Google Places finds reputable open pharmacies; the user approves the list in
  chat. The LLM coordinator itself is deliberately *never* allowed to place
  calls — it proposes call plans, the human approves.
- Approved calls go out through CALL-E's `/v1/calls` API with a strict result
  schema; results fan back in and the aggregator sorts cheapest in-stock first.
- The web UI is a dark Leaflet map + paper chat (light mode included) with a
  MOCK badge and inline Agent:/Pharmacy: transcripts.
- A full mock mode ($0 spend) mirrors the live path so the whole flow is
  demoable and testable — 47 tests.

## Challenges we ran into

Three real ones:

1. Our validator rejected drug searches without a strength (`paracetamol` →
   "invalid input", zero calls placed) — strength had to become optional.
2. Every call failure was swallowed silently into "all calls failed," so we
   added end-to-end CALL-E logging (request, status, truncated body,
   per-pharmacy cause surfaced in the UI).
3. CALL-E 422-rejected our Nigerian demo number: Nigeria isn't in their 22
   supported countries, in any language. No virtual-number trick is free
   (someone always pays the ~$0.23/min NG termination), so we built
   `AGENTRX_MOCK=1`: forced mock pharmacies + mock calls with deterministic
   price/pickup spreads, scripted out-of-stock scenarios, and the same
   watchlist/snapshot path as live.

## Accomplishments that we're proud of

- **Consent-gated calling done right:** no call is ever placed until a human
  approves the list — including from the AI coordinator itself.
- **Parallel calls with structured results** that actually drive UI: sorting,
  verdicts, transcripts, board snapshots.
- **A demo mode with real trade-offs:** varied prices, pickup times, distances,
  delivery flags — the comparison table tells a story on camera.
- **Spend discipline:** 20-call budget protected by a double-tap guard,
  fail-fast validation, and mock-first development.
- **Accessibility touches:** light mode, tappable `tel:` follow-up links,
  one-tap watchlist.

## What we learned

Voice AI's hard edge isn't the conversation — it's coverage: the
region/language matrix decides what you can ship, and the API will tell you
(422 + reason) if you log enough to listen. We also learned that handoff UX
*is* the product for calling agents: when calls fail or stock is out, the
specific reason and the next step matter more than the table. And that a
deterministic mock isn't a shortcut — it's what lets you demo, test, and
iterate without burning call budget.

## What's next for agentRx

- Real Nigerian coverage: a second voice adapter (e.g. Africa's Talking)
  behind the same caller interface for +234 destinations, with CALL-E kept for
  its 22 supported countries.
- A pre-flight region guard so unsupported numbers fail fast with a clear
  message instead of a 21-second 422.
- Scheduled re-checks for stale watches, caregiver SMS summaries, and a pilot
  with one community org running the board weekly.

---

Built with: CALL-E Developer API (`POST /v1/calls` + result schemas),
Strands + Bedrock coordinator, Google Places, FastAPI, Leaflet.
