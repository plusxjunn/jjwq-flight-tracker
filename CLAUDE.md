# CLAUDE.md: SIN ⇄ Bali wedding flight tracker

Briefing for Claude sessions working on this repo. Read README.md for the full user-facing docs.

## What this is
JJ & WQ's wedding is at **Glamp Nusa, Nusa Dua, Bali on Sat 4 Sep 2027**. Check-in 3pm Fri 3 Sep,
check-out 12pm Sun 5 Sep; Glamp Nusa is ~25 min from DPS airport. This tracker checks direct flight
prices SIN ⇄ DPS daily and posts to a **live Telegram channel of wedding guests** ("JJ & WQ's 💍 Bali
Tracker"). Errors go to a separate admin chat (the owner only).

## Hard rules
- **The guest channel is live.** Never post to it, re-pin, or trigger workflows that post (`run`,
  `run-digest`, `welcome`, `announce`) without the owner's explicit go-ahead in this conversation.
- Test guest-facing changes with `python main.py --dry-run --digest` (prints only) and then the
  `preview` workflow mode, which makes real searches but sends every guest message to the admin chat.
  Show the owner the result before going live.
- Don't do real (non-dry-run) runs from a local machine. GitHub Actions owns `data/` and commits it
  daily; a local run would fork the history and miscount the SerpApi quota. Always `git pull` first.
- Never commit `.env` or any key/token. Secrets live in GitHub repository secrets.
- Don't scrape airline sites or get around bot checks. Price checks on airline websites happen
  only in the owner's own browser, as a normal visitor (`/price-check`).
- Stay inside the SerpApi free plan (250/month). Normal use is ~225; the guard skips runs past 235.

## How it runs
- **09:07 SGT daily:** a Claude scheduled task (routine "SIN–Bali wedding flight tracker", with this
  repo attached) runs `gh workflow run track.yml -R plusxjunn/jjwq-flight-tracker --ref main -f mode=scheduled`.
- **11:37 SGT:** a GitHub cron backup. GitHub's own scheduler ran this repo's jobs 2–3h late or not at
  all, so it is only a backup. Both use `--skip-if-done`, which exits with no API calls if today's run
  already succeeded.
- Workflow `.github/workflows/track.yml` modes: `run`, `scheduled`, `preview`, `announce`, `factcheck`,
  `run-digest`, `dry-run`, `test-admin`, `welcome`. After each run the bot commits `data/`, `docs/`,
  `samples/`. Pull/rebase before pushing.
- `docs/report.html` is served by GitHub Pages (main /docs): https://plusxjunn.github.io/jjwq-flight-tracker/report.html

## Design decisions (made with the owner, keep them)
- **Every price is a same-airline return for a specific planned pair of flights** (see `config.yaml`
  `options`). Before 25 Oct 2026 the tracker used Google's cheapest round trip per outbound airline,
  which silently paired SQ out with a Garuda return. That data is legacy (`quotes` table,
  `data/prices_legacy.csv`) and unused.
- **Airlines: Scoot (TR) and Singapore Airlines (SQ) only.** KLM and Garuda were dropped by the owner;
  AirAsia doesn't fly the route (admin is told if QZ/AK reappear).
- **Planned flights:** Scoot TR 288 10:15 / TR 346 21:15 (after work) out, TR 283 19:40 back. SQ
  SQ 938 09:15 / SQ 948 21:35 (after work) out, SQ 945 20:00 back. Chosen for landing from midday and
  flying back in the evening after the 12pm check-out.
- **Date pairs:** Fri 3→Sun 5 (main weekend), Fri 3→Mon 6, Wed 1→Sun 5, Wed 1→Mon 6, Fri 3→Wed 8.
  After-work options are main weekend only.
- **Pricing:** Scoot = two one-way fares (5 one-way searches a day), because Scoot prices each flight
  separately. SQ = return-ticket search per pair with `include_airlines=SQ` plus `outbound_times` and
  `return_times` hour windows; main-weekend pair daily, others every 4 days. `include_airlines` also
  returns partner codeshares (e.g. Garuda), so `price_pairs` matches the exact flight number. If Google
  lists a pair without a price it is skipped and reported as "no price yet", never guessed.
- **Alerts:** 30-day low (≥14 days of data, ≥10% under the 30-day median), sudden drop ≥12% vs the
  previous check, optional target. 72h cooldown per pair unless a further 5% drop. "Rising" = ≥15%
  above median for the last 3 checks. Google's price_level isn't used (it rates the whole route).
- **One run a day.** The evening runs were dropped, since fares this far out don't move within a day.
- **Fact checks (26 Oct 2026):** Scoot's own price matched exactly (S$228 + S$194 = S$422). SQ's own
  website price is not yet verified; Google doesn't show it and travel agents agree on ~S$611–613.

## Guest message style (approved by the owner; preview any change)
- Telegram HTML: `<b>`, `<i>`, `<a>` only; escape dynamic text with `html.escape`.
- 12-hour times (`9:15am`), routes as `SIN → DPS` / `DPS → SIN`, prices `S$422 per person, return`.
- Deal alert: headline "Good time to book: <Airline>", dates, bold price, reason, both flights, bag
  note, "👉 Book now: Google Flights". Several pairs the same day share one message.
- Sunday summary: chart picture first, then one block per airline with **its own verdict** (🔥 Book
  now, ✅ Good price, ⏳ Wait, 📈 Rising, 👀 Still tracking), times once, every date pair priced. The
  closing line only recommends airlines worth booking. A rising or above-usual price never says
  "good time to book".
- Scoot = cabin bag only (~S$80 more for 20kg both ways); SQ includes a checked bag.

## Code map
`config.yaml` settings · `main.py` scheduling, pair pricing, alerts, digest, CLI · `fetch.py` SerpApi /
sample providers · `store.py` SQLite (`pair_quotes` is the live table) · `rules.py` pure alert/verdict
logic · `notify.py` Telegram + all guest wording · `report.py` Pages report + chart PNG ·
`pricecheck.py` checklist of latest prices · `factcheck.py` Google booking-step check (~10 searches).

```bash
pip install -r requirements.txt
python -m unittest discover -s tests          # 47 tests
python main.py --dry-run --digest             # no API calls, prints guest messages
python pricecheck.py                          # latest tracker prices to compare with airline sites
```

## Commits
Small changes go straight to `main` (owner's choice). End commit messages with the Claude attribution
lines your session uses. No model names in commits.
