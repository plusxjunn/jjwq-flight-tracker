# SIN ⇄ Bali wedding flight tracker

Tracks nonstop economy fares **Singapore (SIN) ⇄ Bali (DPS)** for our wedding on **Sat 4 Sep 2027**,
stores its own price history, and posts plain-English alerts to a Telegram channel guests can join.
Runs on GitHub Actions; no server needed.

- All prices are **per person, economy, 1 adult, nonstop**, and are the airline's **cheapest fare type**
  (Scoot's basic fare, SQ Economy Lite, KLM/Garuda's lowest economy fare), as shown by Google Flights.
- Airlines tracked: **Scoot (TR), Singapore Airlines (SQ), KLM (KL), Garuda Indonesia (GA)**.
  AirAsia is not tracked: Indonesia AirAsia stopped flying Singapore–Bali nonstop in July 2026.
  If AirAsia (QZ/AK) shows up on the route again, the admin chat gets a note.

## What is tracked

| ID | Trip | Runs |
|---|---|---|
| `core` | Round trip Fri 3 → Sun 5 Sep | daily 09:07 SGT; also 21:07 SGT from 1 Mar 2027 |
| `flex` | Round trip Thu 2 → Mon 6 Sep | daily 09:07 SGT |
| `out_wed`, `out_fri` | One-way SIN→Bali Wed 1 / Fri 3 Sep | daily 09:07 SGT |
| `back_sun`, `back_mon` | One-way Bali→SIN Sun 5 / Mon 6 Sep | daily 09:07 SGT |

For each search we store, per airline, the cheapest nonstop (price, flight number, times), plus the
cheapest **afternoon/evening** option (lands in Bali 12:00–23:59 / leaves Bali 12:00–23:59; edit
`time_windows` in `config.yaml`). The one-way legs drive the **mix & match** line in the digest
(e.g. Scoot out + SQ back).

### SerpApi budget (free plan = 250 searches/month)

| Period | Searches/day | ≈ per month |
|---|---|---|
| now → 28 Feb 2027 | 6 | 186 |
| 1 Mar → 3 Sep 2027 | 6 + 1 (evening `core`) | 217 |

A hard guard skips any run that would take the month past **235** calls and tells the admin chat
(leaves ~15 for manual runs). A warning goes to the admin chat at 200. Every call is logged in
`data/tracker.db` (`api_calls`). Runs stop automatically after 3 Sep 2027; disable the workflow after.

## Alerts (guest channel)

Checked for every trip × airline. An alert is posted when **any** of these is true:

1. **Google says low:** Google rates the route "low" and the price is under Google's typical range.
2. **Lowest in 30 days:** with ≥14 days of our own data, the price is at the 30-day low and ≥10% below the 30-day median.
3. **Seat sale:** ≥12% cheaper than the previous run.
4. **Target hit:** only if `rules.target_price` is set in `config.yaml`.

Each trip+airline then has a 72-hour cooldown, unless the price drops a further 5%.
A **"prices climbing"** warning is posted when the cheapest `core` price has been ≥15% above its
30-day median for 3 days in a row.

**Weekly digest** every Sunday morning: cheapest round trip per airline (both trips), mix & match
combos, afternoon/evening options, change vs last week, lowest seen, and one verdict:

| Verdict | Meaning |
|---|---|
| **BOOK NOW** | Genuinely low (an alert rule fired). After 31 May 2027: any price at/below the 30-day median. |
| **GOOD PRICE** | At/below the 30-day median, or Google says "low". Fine to book. |
| **WAIT** | Higher than usual. No action needed. |
| **RISING** | Prices climbing. Book soon. |

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
  fares add 20 kg. The digest uses `bag_fee_sgd` (default **S$40 each way**) as a rough add-on. Edit it in `config.yaml`.
- **Singapore Airlines** Economy Lite includes a checked bag. Advance seat choice costs extra.
- **KLM / Garuda** are full-service. Their cheapest fare usually includes a checked bag, but confirm at booking.

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
3. `welcome`: posts and pins the welcome message in the guest channel.
4. `run`: a real run (6 SerpApi calls).

After that it runs by itself every day.

## Day-to-day

- **Change settings:** edit `config.yaml` on GitHub (target price, bag fee, time windows, thresholds).
- **Data:** `data/tracker.db` (SQLite) plus `data/prices.csv`, `data/runs.csv` and `data/alerts.csv`,
  committed after every run. Every run (ok / partial / api_error / quota_skip / error) is logged in `runs`.
- **Errors:** API errors, quota skips and crashes go to the admin chat only. Guests never see them.

## Code

| File | What it does |
|---|---|
| `config.yaml` | trips, airlines, time windows, quota, rule thresholds |
| `fetch.py` | `FlightProvider` interface + `SerpApiProvider` (Google Flights via SerpApi) + `SampleProvider` (dry run) |
| `store.py` | SQLite schema, call counter, run log, CSV export |
| `rules.py` | alert rules, cooldown, "prices climbing", verdict (pure functions) |
| `notify.py` | Telegram guest/admin delivery and message templates |
| `report.py` | builds `docs/report.html` (Chart.js, mobile-first) |
| `main.py` | orchestration and CLI |
| `tests/test_rules.py` | unit tests for every rule, cooldown and the hard-deadline verdict |

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
python main.py --dry-run --slot morning --digest     # zero API calls, prints messages
python main.py --dry-run --test-admin                # print the admin test message
```

To swap data providers, implement `FlightProvider.search()` in `fetch.py` and return `SearchResult`.
(Amadeus Self-Service was decommissioned in July 2026. Don't scrape Google Flights directly.)
