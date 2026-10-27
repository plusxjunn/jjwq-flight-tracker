---
description: Compare the tracker's latest prices with Scoot's and Singapore Airlines' own websites, in the user's browser
---

Check the flight tracker's prices against the airlines' own websites.

1. Run `git pull` so the price data is current, then `python pricecheck.py` (add `--main` if the user
   only wants the main weekend: $ARGUMENTS). It prints each planned flight pair with the tracker's
   latest price and the fare to pick.
2. In the user's browser (built-in browser or Claude in Chrome), for each row, as a normal visitor:
   - **Scoot:** flyscoot.com · Return · Singapore (SIN) → Denpasar Bali (DPS) · 1 adult. Pick exactly
     the listed flights and the **cheapest fare** (cabin bag only, no checked bag). Note the total in SGD.
   - **Singapore Airlines:** singaporeair.com · Return · Singapore → Denpasar Bali · 1 adult ·
     Economy. Pick exactly the listed flights and **Economy Lite**. Note the total in SGD.
   - Rows marked "no price yet": just note whether the site sells that combination.
3. Rules: don't log in, don't enter any personal or payment details, stop at the flight-selection or
   fare page. If a site shows a CAPTCHA or blocks the browser, stop and tell the user. Don't try to
   get around bot checks. Ask the user to do that step themselves.
4. Report one table: pair, flights, site price, tracker price, difference, fare name seen. Then say
   plainly whether the tracker is accurate. A few dollars' difference is normal: prices move during
   the day, and the tracker checks once a day around 09:07 SGT.
5. If any price differs by more than ~5% or a flight isn't sold, investigate (config.yaml flight
   numbers and times, main.price_pairs, the saved responses in samples/) and propose a fix. Don't
   change code, post to Telegram, or trigger GitHub workflows without asking the user first.
