"""Print the tracker's latest price for every planned flight pair, as a checklist for comparing
against the airlines' own websites (see .claude/commands/price-check.md).

    python pricecheck.py            # all pairs
    python pricecheck.py --main     # main weekend only
"""
from __future__ import annotations

import sys
from datetime import date

import main
from store import Store


def dlabel(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%a} {d.day} {d:%b} {d.year}"


def rows(cfg: dict, store: Store, main_only: bool = False) -> list[dict]:
    out = []
    for p in main.pairs(cfg):
        if main_only and p["trip"]["id"] != cfg["trips"][0]["id"]:
            continue
        q = store.latest_pair(p["pair_id"])
        out.append({
            "pair": p["pair_id"],
            "airline": cfg["airlines"][p["airline"]],
            "out": f"{dlabel(p['trip']['out'])} {p['out_flight']} {q['out_dep'] if q else cfg['flights'][p['out_flight']][0]}",
            "back": f"{dlabel(p['trip']['back'])} {p['back_flight']} {q['back_dep'] if q else cfg['flights'][p['back_flight']][0]}",
            "price": f"S${q['price']:,.0f}" if q else "no price yet",
            "checked": q["day"] if q else "-",
            "fare": "cheapest fare (cabin bag only)" if p["airline"] in cfg["budget_airlines"] else "Economy Lite",
        })
    return out


if __name__ == "__main__":
    cfg = main.load_config()
    store = Store(main.DB_PATH)
    print("| Pair | Airline | SIN → DPS | DPS → SIN | Fare to pick | Tracker price | Checked |")
    print("|---|---|---|---|---|---|---|")
    for r in rows(cfg, store, "--main" in sys.argv):
        print(f"| {r['pair']} | {r['airline']} | {r['out']} | {r['back']} | {r['fare']} | {r['price']} | {r['checked']} |")
