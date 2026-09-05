# Plan: a five-slide deck

`docs/DECK.md` is ten slides written to be read. This is the plan for the deck
that gets *presented* — five slides, because a hackathon judging slot is three
minutes and a slide you skip is a slide that cost you time.

The deck is not the demo. The demo is the product on screen. The deck's whole
job is to make a sceptical judge want to look at the screen, and to leave them
with the one sentence that distinguishes this build from every other team's.

## The single claim the deck must land

> Deterministic Python computes every figure, verdict and cost. The model
> chooses which question to ask, resolves what the controller meant, and
> explains the result. It never calculates.

Everything on every slide either sets this up, proves it, or shows what it buys.
If a slide does none of those, it comes out.

## The five slides

### 1 — The desk at 06:00

One photograph-quality sentence and nothing else. A captain calls in sick for a
two-day pairing. The controller has minutes, seven rules, 150 crew and a
spreadsheet.

- **Visual:** full-bleed screenshot of the landing view, dimmed, with the
  headline over it.
- **Says:** this is a real job with a real clock on it.
- **Speaker note:** the system exists for one moment — the one where somebody
  acts on a number.

### 2 — The real question is architectural

Not "can a model do crew control" — it cannot, and should not. The question is
**what should the model decide, and what should deterministic code decide.**
Then the answer, stated as the claim above.

- **Visual:** the boundary diagram, exported from the running UI (the side
  button). Left column what the engine decides, right column what the model
  decides.
- **Says:** we made a choice other teams did not have to make explicitly, and
  it is the choice the product is built on.

### 3 — How the claim is enforced

The interesting half. A prompt asking the model not to invent numbers is a
request. This is a mechanism:

- every tool returns `{summary, claims, missing, data}`;
- the model may state a figure only by citing a claim id;
- the gate substitutes the engine's own text before the reply is sent, so a
  *real* number cannot end up under the wrong label — the failure a spot-check
  misses;
- `missing` says what a result does not establish, so an impact answer cannot
  be read as a recommendation.

- **Visual:** one real tool result next to the sentence it produced — the JSON
  claim on the left, the rendered answer on the right, an arrow between them.
  Real output, not a mock.
- **Says:** we can prove the boundary holds, not just assert it.

### 4 — What it buys the controller

The product, in one screen: the recommendation, the rule that cleared it, the
cost, and why nineteen other people were ruled out. Then the two distinctions
that decide whether an answer is usable:

- **illegal is not the same as uncallable** — a reserve outside their on-call
  window has broken no rule;
- **day 1 is not the pairing** — C-3305 is legal for the first day of P-2291
  and breaches the 7-day duty limit on the second.

- **Visual:** the Q31 screenshot (ranked cover with exclusions open).
- **Says:** the hard part of this domain is not retrieval, and we handled the
  hard part.

### 5 — Coverage, and what we would do next

38 published questions, all three tiers, one loop, no per-question handling.
Then two or three honest next steps — the ones in the review, not aspirational
features.

- **Visual:** a compact tier table (16 / 14 / 8) with the tools that answer
  each, plus the test counts.
- **Says:** this is finished work, and we know where its edges are.

## What to cut, and why

- No architecture diagram with boxes and arrows for its own sake — slide 2's
  boundary diagram is the only structural visual that earns its place.
- No dataset tour. The judges wrote the dataset.
- No agent-loop walkthrough. It is a tool-calling loop; nobody is surprised.
- No roadmap slide. Slide 5's last line covers it in three bullets.

## Files needed from the repo

**Source material — the words come from these, do not rewrite from memory:**

| File | What it supplies |
|---|---|
| `docs/DESCRIPTION.md` | Slides 1, 4 and 5 nearly verbatim; the tier-by-tier coverage argument |
| `docs/DECK.md` | The ten-slide version — slides 1–3 here are compressions of its 1–6 |
| `docs/TOOLS.md` | The claim-envelope wording for slide 3 |
| `docs/TOOL_DESIGN.md` | Why tools read several files at once, if a judge asks |
| `docs/THE_38_QUESTIONS.md` | The tier counts and what each check is, for slide 5 |
| `problem_statement/README.md` | Dataset shape: 147 legs, 150 crew, 39 pairings, 16 reserves |
| `problem_statement/data/questions.json` | The authoritative 16 / 14 / 8 tier split |

**Assets — images the slides place:**

| File | Used on |
|---|---|
| `screenshots/landing.png` | Slide 1 background |
| `screenshots/boundary.png` | Slide 2 — export from the UI's boundary button |
| `screenshots/Q31.png` | Slide 4 — ranked cover, exclusions open |
| `screenshots/claim-to-answer.png` | Slide 3 — compose from a real `/api/tool` response and the answer it produced |

The 38-question sweep already produces `Q01.png` … `Q38.png`; slide 4's image
is `Q31.png` from that run. `screenshots/` in the repo is currently empty — the
sweep writes to the scratchpad, so the four images above need copying in before
the deck build, and they belong in the repo anyway (`FINAL.md` §1.3 asks for
them).

**Live for the numbers, so nothing on a slide is stale:**

| File | Supplies |
|---|---|
| `aircrew/tools.py` | The ten tool names and tiers for slide 5's table |
| `tests/test_agent_loop.py` | The loop/gate test count |
| `tests/ui_check.js` | The UI check count |

## How to build it

`python-pptx` is not installed and is not a project dependency — the engine is
standard library only and stays that way. So install it into the scratchpad
venv, not the project:

```bash
python -m pip install --user python-pptx
```

Then a single build script, `scratchpad/build_deck.py`, that:

1. reads the tier counts from `problem_statement/data/questions.json` and the
   tool names from `aircrew.tools.SCHEMAS`, so slide 5 cannot go stale;
2. lays out 16:9 slides at 13.333 × 7.5 in;
3. uses the product's own palette — near-black ground, off-white text, the same
   blue for computed, amber for the acted-on figure, red for a rule that stops
   you — so the deck and the screenshots do not look like two products;
4. sets two type sizes and no more: 40pt headline, 18pt body. A slide that
   needs a third size has too much on it;
5. writes speaker notes into the notes pane, not onto the slide;
6. outputs `docs/crew-ops-advisor.pptx`.

Keep the script; the deck will change twice before the demo and rebuilding it by
hand is how a stale number reaches a slide.

## Order of work

1. Copy the four images into `screenshots/` (and take the claim-to-answer one,
   which does not exist yet).
2. Draft the five slides as text in this file's structure, and read them aloud
   against a three-minute clock.
3. Write `build_deck.py`, generate, and check it opens in PowerPoint rather
   than only in a viewer.
4. Re-generate after the Astra and UI reviews land, since slide 5's last three
   bullets should be the real findings.
