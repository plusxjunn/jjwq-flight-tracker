# SIN ⇄ Bali wedding flight tracker

Tracks direct economy fares **Singapore (SIN) ⇄ Bali (DPS)** for our wedding at **Glamp Nusa, Nusa Dua**
on **Sat 4 Sep 2027**, stores its own price history, and posts plain-English alerts to a Telegram channel
guests can join. Runs on GitHub Actions; no server needed.

- Every price is a **same-airline return** for a **specific pair of flights**, per person, economy, 1 adult.
  (Until 24 Oct 2026 the tracker used Google's cheapest round trip per outbound airline, which could pair
  an SQ flight out with a Garuda flight back. That older data is kept in `data/prices_legacy.csv` but not used.)
- Airlines: **Scoot (TR) and Singapore Airlines (SQ)**. KLM and Garuda were dropped by choice. AirAsia isn't
  tracked (Indonesia AirAsia stopped flying Singapore–Bali nonstop in July 2026); if QZ/AK reappears on
  the route, the admin chat gets a note.

## What is tracked

**Planned flights.** Picked for the 3pm check-in / 12pm check-out at Glamp Nusa (~25 min from DPS):
land in Bali from midday, or leave Singapore after work; leave Bali in the evening.

| Airline | Option | SIN → DPS | DPS → SIN |
|---|---|---|---|
| Scoot | daytime | TR 288 · 10:15 → 13:05 | TR 283 · 19:40 → 22:35 |
| Scoot | after work (main weekend only) | TR 346 · 21:15 → 00:05 | TR 283 · 19:40 → 22:35 |
| SQ | daytime | SQ 938 · 09:15 → 12:00 | SQ 945 · 20:00 → 22:40 |
| SQ | after work (main weekend only) | SQ 948 · 21:35 → 00:10 | SQ 945 · 20:00 → 22:40 |

**Date pairs:** Fri 3 → Sun 5 (main weekend), Fri 3 → Mon 6, Wed 1 → Sun 5, Wed 1 → Mon 6, Fri 3 → Wed 8.

**How prices are found** (all in `config.yaml`):
- **Scoot** prices each flight separately, so its return = two one-way fares. **5 one-way searches a day**
  (Wed 1 / Fri 3 to Bali; Sun 5 / Mon 6 / Wed 8 back) give the exact price of each planned Scoot flight.
- **SQ** return tickets are much cheaper than two one-ways, so each pair gets its own
  **return-ticket search restricted to that airline and those departure hours** (`include_airlines`,
  `outbound_times`, `return_times`). The main-weekend daytime pair is checked daily, the other
  5 SQ pairs every 4 days, spread across the days.
- If a planned flight isn't listed for a date (schedule change, not on sale yet), it is skipped and
  the admin chat is told. Nothing is guessed.

### SerpApi budget (free plan = 250 searches/month)

About **225 searches a month**: 5 one-ways a day (155) + about 70 SQ return-ticket searches. The first run
prices everything at once (11 searches). A hard guard skips any run that would take the month past
**235** and tells the admin chat; a warning goes out at 230. Every call is logged in `data/tracker.db`
(`api_calls`). Runs stop after 3 Sep 2027; disable the workflow after.

### When it runs

GitHub's own scheduler often starts runs hours late or drops them, so the 09:07 SGT run is started by a
**Claude scheduled task** (a routine with this repo attached) that triggers the workflow with mode
`scheduled`. GitHub's cron is kept only as a **backup** at 11:37 SGT. Both use `--skip-if-done`, so if
today's run already happened the backup exits straight away with no API calls. Fares this far out don't
move within a day, so there's one run a day.

## Messages (guest channel)

All messages use Telegram HTML (bold, italics, links), 12-hour times and `SIN → DPS` / `DPS → SIN`.

**Deal alerts**, checked for every flight pair. Posted when **any** of these is true:
1. **Lowest in 30 days:** with ≥14 days of data, at the 30-day low and ≥10% below the 30-day median.
2. **Sudden drop:** ≥12% cheaper than the previous check of that pair.
3. **Target hit:** main weekend only, if `rules.target_price` is set.

Each pair then has a 72-hour cooldown, unless the price drops a further 5%. Pairs that fire on the same
day share one message, one block per airline and flight option. (Google's "price level" is no longer
used: it rates the whole route, not one airline's specific flights.)

**Prices rising:** posted when an airline's main-weekend daytime price has been ≥15% above its 30-day
median for its last 3 checks.

**Sunday summary**, after a chart picture of the main-weekend daytime price per airline (▲ = alert sent).
One block per airline: its verdict and why, its flight times, its price for every date pair (with
"checked Thu" when not checked today), the after-work option, and its bag rule. The closing line only
recommends airlines worth booking, so a rising price never says "good time to book".

| Verdict (per airline) | Meaning |
|---|---|
| 🔥 **Book now** | An alert rule fires. From 1 Jun 2027: any price at or below its usual. |
| ✅ **Good price** | At or below its 30-day median. |
| ⏳ **Wait** | Above its usual price. |
| 📈 **Rising** | ≥15% above usual for 3 checks in a row. |
| 👀 **Still tracking** | Under 2 weeks of prices for that airline. |

## Strategy notes baked in

- **Fri 3 Sep 2027 is Teachers' Day and the last day of MOE Term III.** The September school
  holidays run **Sat 4 – Sun 12 Sep 2027** (Term IV starts Mon 13 Sep), per MOE's
  [2027 school terms release](https://www.moe.gov.sg/news/press-releases/20260714-school-terms-and-holidays-for-2027).
  Families will be flying out that exact Friday, so expect higher-than-normal demand.
- Friday-out / Sunday-return are usually the priciest days of the week on this route.
- Generic "book 2–6 weeks ahead" advice doesn't apply to a peak holiday weekend.
  `rules.hard_deadline: 2027-05-31`: after that date the verdict is BOOK NOW unless the price is
  above its 30-day median.

### Baggage and seats (check at booking)

- **Scoot**'s cheapest fare: 10 kg cabin bag only. **No checked bag, no seat choice.** The bundle
  fares add 20 kg. Messages use `bag_fee_sgd` (default **S$40 each way**) as a rough add-on. Edit it in `config.yaml`.
- **Singapore Airlines** Economy Lite includes a checked bag. Advance seat choice costs extra.

## Setup (one time)

### 1. Create the Telegram bot
1. In Telegram, open **@BotFather** → send `/newbot` → pick a name (e.g. *JJWQ Flight Alerts*) and a
   username ending in `bot`.
2. BotFather replies with a **token** like `123456:ABC-…`. That's `TELEGRAM_BOT_TOKEN`.

### 2. Create the guest channel
1. Telegram → **New Channel** → name it (e.g. *SIN ⇄ Bali Wedding Flights*). Public or private both work.
2. Channel → **Administrators → Add Admin** → search your bot's username → enable **Post messages**
   and **Pin messages** (pinning is needed for the welcome post) → Save.

### 3. Get the IDs
- **Channel ID** (`TELEGRAM_CHANNEL_ID`):
  - *Public channel:* use `@yourchannelname`.
  - *Private channel:* post any message in the channel, then open
    `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find `"chat":{"id":-100…`.
    That number, including `-100`, is the ID.
- **Admin chat ID** (`TELEGRAM_ADMIN_CHAT_ID`): open a private chat with your bot and send `/start`,
  then open the same `getUpdates` URL and copy `"chat":{"id":…}` from your message (a positive number).

### 4. Add GitHub secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**, one for each:
`SERPAPI_KEY` (64 characters, from serpapi.com/manage-api-key, no quotes), `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHANNEL_ID`, `TELEGRAM_ADMIN_CHAT_ID`.

### 5. Allow the workflow to commit
Repo → **Settings → Actions → General → Workflow permissions** → **Read and write permissions** → Save.

### 6. Enable GitHub Pages (the price chart)
Repo → **Settings → Pages** → Source **Deploy from a branch** → Branch `main`, folder **`/docs`** → Save.
After the first run the chart is at `https://<user>.github.io/<repo>/report.html` (set `report_url`
in `config.yaml` if it differs).

> GitHub Pages on a **private** repo needs a paid GitHub plan. On a free account, make the repo
> public or skip the chart link. The repo contains no secrets, only prices.

### 7. Test, then go live
Repo → **Actions → track → Run workflow**, choose a mode:
1. `test-admin`: you should get a test message in your private chat with the bot.
2. `dry-run`: uses saved sample data, makes no API calls, and prints all messages in the job log.
3. `preview`: real searches (all 11), but every guest message goes to your admin chat, marked PREVIEW.
   Also saves the raw responses as dry-run samples.
4. `welcome`: posts and pins the welcome message (replacing any pinned post).
5. `announce`: posts `announcement.html` to the guest channel (for one-off notices).
6. `run`: a normal run.

Then create the daily **Claude scheduled task** (09:07 Asia/Singapore, this repo attached) that runs
`gh workflow run track.yml -R <owner>/<repo> --ref main -f mode=scheduled`.

## Day-to-day

- **Change settings:** edit `config.yaml` on GitHub (flights, date pairs, target price, bag fee, thresholds).
- **Data:** `data/tracker.db` (SQLite) plus `data/prices.csv` (one row per flight pair per check),
  `data/runs.csv` and `data/alerts.csv`,
  committed after every run. Every run (ok / partial / api_error / quota_skip / error) is logged in `runs`.
- **Errors:** API errors, quota skips and crashes go to the admin chat only. Guests never see them.

## Code

| File | What it does |
|---|---|
| `config.yaml` | airlines, planned flights, date pairs, search frequency, quota, rule thresholds |
| `fetch.py` | `FlightProvider` interface + `SerpApiProvider` (Google Flights via SerpApi) + `SampleProvider` (dry run) |
| `store.py` | SQLite schema, call counter, run log, CSV export |
| `rules.py` | alert rules, cooldown, "prices climbing", verdict (pure functions) |
| `notify.py` | Telegram guest/admin delivery and message templates |
| `report.py` | builds `docs/report.html` (Chart.js, mobile-first) and the weekly chart picture (matplotlib) |
| `main.py` | orchestration and CLI |
| `tests/` | rules (every alert rule, cooldown, rising, verdicts, hard deadline), flight-pair pricing, search schedule and budget, message wording, the backup-run guard |

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py --dry-run --digest                    # zero API calls, prints messages
python main.py --dry-run --test-admin                # print the admin test message
```

To swap data providers, implement `FlightProvider.search()` in `fetch.py` and return `SearchResult`.
(Amadeus Self-Service was decommissioned in July 2026. Don't scrape Google Flights directly.)
