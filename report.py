"""Static report page (docs/report.html) for GitHub Pages."""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta
from pathlib import Path

# Fixed categorical order (validated palette): colour follows the airline, never its rank.
COLORS_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
COLORS_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500"]


def _series(store, cfg):
    """Main-weekend daytime return price per airline per day, plus alert markers."""
    main = cfg["trips"][0]["id"]
    days: list[str] = []
    per_airline: dict[str, dict[str, float]] = {c: {} for c in cfg["airlines"]}
    for r in store.db.execute("SELECT day, airline, MIN(price) p FROM pair_quotes WHERE trip=? AND option='day'"
                              " GROUP BY day, airline ORDER BY day", (main,)):
        if r["day"] not in days:
            days.append(r["day"])
        if r["airline"] in per_airline:
            per_airline[r["airline"]][r["day"]] = r["p"]
    alerts = [{"day": r["ts"][:10], "airline": r["airline"], "price": r["price"]}
              for r in store.db.execute("SELECT ts, airline, price FROM alerts WHERE kind='deal' AND search_id=?"
                                        " || ':' || airline || ':day'", (main,))]
    return {
        "days": days,
        "airlines": [{"code": c, "name": n, "prices": [per_airline[c].get(d) for d in days]}
                     for c, n in cfg["airlines"].items()],
        "bandLow": [None] * len(days),
        "bandHigh": [None] * len(days),
        "alerts": alerts,
    }


def _t12(hhmm: str) -> str:
    hr, mi = int(hhmm[:2]), hhmm[3:5]
    return f"{(hr % 12) or 12}:{mi}{'am' if hr < 12 else 'pm'}"


def _dates(t: dict) -> str:
    from datetime import date
    o, b = date.fromisoformat(t["out"]), date.fromisoformat(t["back"])
    return f"{o:%a} {o.day} → {b:%a} {b.day}"


def _table(store, cfg) -> str:
    main = cfg["trips"][0]["id"]
    head = "".join(f'<th scope="col">{html.escape(cfg["short_names"].get(c, c))}</th>' for c in cfg["airlines"])
    times = []
    for c in cfg["airlines"]:
        q = store.latest_pair(f"{main}:{c}:day")
        times.append(f'<td><small>{_t12(q["out_dep"])} → {_t12(q["out_arr"])}<br>back {_t12(q["back_dep"])}</small></td>'
                     if q else '<td class="na">–</td>')
    rows = [f'<tr><th scope="row">Flights</th>{"".join(times)}</tr>']
    for t in cfg["trips"]:
        cells = []
        for c in cfg["airlines"]:
            q = store.latest_pair(f"{t['id']}:{c}:day")
            cells.append(f'<td>S${q["price"]:,.0f}<small>checked {q["day"][8:]}/{q["day"][5:7]}</small></td>'
                         if q else '<td class="na">–</td>')
        label = (t.get("label", "") + "<br>" if t.get("label") else "") + _dates(t)
        rows.append(f'<tr><th scope="row">{label}</th>{"".join(cells)}</tr>')
    cells = []
    for c in cfg["airlines"]:
        q = store.latest_pair(f"{main}:{c}:after_work")
        cells.append(f'<td>S${q["price"]:,.0f}<small>out {_t12(q["out_dep"])}</small></td>' if q
                     else '<td class="na">–</td>')
    rows.append(f'<tr><th scope="row">After work<br>{_dates(cfg["trips"][0])}</th>{"".join(cells)}</tr>')
    return f'<table><thead><tr><th scope="col">Return, per person</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def chart_png(store, cfg, path: str | Path) -> bool:
    """Weekly-digest chart picture of the core trip. Returns False when there's no data yet."""
    data = _series(store, cfg)
    if not data["days"]:
        return False
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    bg, ink, ink2, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
    days = [datetime.fromisoformat(d) for d in data["days"]]
    few = len(days) < 15
    fig, ax = plt.subplots(figsize=(7.2, 4.8), dpi=150)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    band = [(d, lo, hi) for d, lo, hi in zip(days, data["bandLow"], data["bandHigh"]) if lo and hi]
    if len(band) > 1:
        ax.fill_between([b[0] for b in band], [b[1] for b in band], [b[2] for b in band],
                        color="#78786e", alpha=.14, lw=0, label="Google's typical range")
    elif band:   # one day of data: show the range as a flat band
        ax.axhspan(band[0][1], band[0][2], color="#78786e", alpha=.14, lw=0, label="Google's typical range")
    ends, color_of = [], {}
    for i, a in enumerate(data["airlines"]):
        pts = [(d, p) for d, p in zip(days, a["prices"]) if p is not None]
        if not pts:
            continue
        color_of[a["code"]] = COLORS_LIGHT[i]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=COLORS_LIGHT[i], lw=2.2,
                marker="o" if few else None, ms=4, label=a["name"])
        ends.append((pts[-1][1], pts[-1][0], cfg["short_names"].get(a["code"], a["code"])))
    for al in data["alerts"]:
        ax.plot(datetime.fromisoformat(al["day"]), al["price"], marker="^", ms=8, ls="none",
                color=color_of.get(al["airline"], ink2), mec=bg, mew=1.5)
    if data["alerts"]:
        ax.plot([], [], marker="^", ms=8, color=ink2, ls="none", label="Alert sent")

    span = max((days[-1] - days[0]).days, 6)
    ax.set_xlim(days[0] - timedelta(days=0.5), days[-1] + timedelta(days=span * 0.22))
    # Direct labels at the line ends, nudged apart so they never overlap.
    ends.sort()
    lo, hi = ax.get_ylim()
    ys = [e[0] for e in ends]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + (hi - lo) * 0.055)
    for (price, d, name), y in zip(ends, ys):
        ax.annotate(f"{name} S${price:.0f}", (d, price), xytext=(mdates.date2num(d) + span * 0.03, y),
                    textcoords="data", va="center", fontsize=9.5, color=ink, fontweight="bold")

    ax.set_title("Main weekend, Fri 3 → Sun 5 Sep · return per person", loc="left",
                 fontsize=12, fontweight="bold", color=ink)
    ax.yaxis.set_major_formatter(lambda v, _: f"S${v:.0f}")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%-d %b"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, round(span / 6))) if span <= 21
                               else mdates.AutoDateLocator(maxticks=7))
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#cfcec9")
    ax.grid(axis="y", color=grid)
    ax.tick_params(colors=ink2, labelsize=9, length=0)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3, frameon=False, fontsize=9, labelcolor=ink2)
    fig.tight_layout()
    fig.savefig(path, facecolor=bg)
    plt.close(fig)
    return True


def build(store, cfg, path: str | Path, now: datetime) -> None:
    data = _series(store, cfg)
    page = TEMPLATE.replace("__DATA__", json.dumps(data)) \
        .replace("__TABLE__", _table(store, cfg)) \
        .replace("__UPDATED__", f"{now:%d %b %Y, %H:%M} SGT") \
        .replace("__COLORS_LIGHT__", json.dumps(COLORS_LIGHT)) \
        .replace("__COLORS_DARK__", json.dumps(COLORS_DARK)) \
        .replace("__BAG__", f'{" & ".join(cfg["short_names"][c] for c in cfg.get("budget_airlines", []))} is cabin bag only '
                            f'(about S${2 * cfg.get("bag_fee_sgd", 40):.0f} more for a 20kg bag both ways). SQ includes a checked bag.')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(page)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SIN⇄Bali Fares</title>
<style>
:root {
  color-scheme: light;
  --surface: #fcfcfb; --surface-2: #f3f2ef; --text: #0b0b0b; --text-2: #52514e; --muted: #8a8984;
  --grid: #e6e5e1; --band: rgba(120,120,110,.16); --border: #dcdbd6;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface: #1a1a19; --surface-2: #242423; --text: #f0efec; --text-2: #c3c2b7; --muted: #8f8e88;
    --grid: #333331; --band: rgba(200,200,190,.14); --border: #3a3a38;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface: #1a1a19; --surface-2: #242423; --text: #f0efec; --text-2: #c3c2b7; --muted: #8f8e88;
  --grid: #333331; --band: rgba(200,200,190,.14); --border: #3a3a38;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--text);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 20px 16px 40px; }
h1 { font-size: 1.3rem; margin: 0 0 4px; }
h2 { font-size: 1.05rem; margin: 28px 0 8px; }
p.sub { color: var(--text-2); margin: 0 0 16px; }
.chart-wrap { position: relative; height: 320px; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 10px 0 0; padding: 0; list-style: none;
  color: var(--text-2); font-size: .85rem; }
.legend i { display: inline-block; width: 14px; height: 3px; border-radius: 2px; vertical-align: middle; margin-right: 6px; }
.legend i.band { height: 10px; background: var(--band); }
.legend i.tri { width: 0; height: 0; border-left: 6px solid transparent; border-right: 6px solid transparent;
  border-bottom: 10px solid var(--text-2); border-radius: 0; }
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table { border-collapse: collapse; width: 100%; min-width: 520px; font-size: .9rem; }
th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--border); vertical-align: top; }
thead th { color: var(--text-2); font-weight: 600; font-size: .8rem; }
tbody th { font-weight: 500; }
td small { display: block; color: var(--muted); font-size: .75rem; }
td small.pref { color: var(--text-2); }
td.na { color: var(--muted); }
.note { color: var(--text-2); font-size: .85rem; background: var(--surface-2); padding: 10px 12px; border-radius: 8px; }
.empty { color: var(--text-2); padding: 40px 0; text-align: center; }
</style>
</head>
<body>
<main>
  <h1>Singapore ⇄ Bali fares</h1>
  <p class="sub">Glamp Nusa, Sat 4 Sep 2027 · direct flights · per person, return · updated __UPDATED__</p>

  <h2>Main weekend, Fri 3 → Sun 5 Sep: daytime flights, same airline both ways</h2>
  <div class="chart-wrap"><canvas id="chart" aria-label="Cheapest round-trip price per airline per day"></canvas></div>
  <ul class="legend" id="legend"></ul>

  <h2>Latest prices</h2>
  <div class="scroll">__TABLE__</div>
  <p class="note">SIN = Singapore Changi · DPS = Bali (Denpasar). Main weekend checked daily; other SQ dates every few days.<br>🧳 __BAG__</p>
</main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
const DATA = __DATA__;
const dark = matchMedia('(prefers-color-scheme: dark)').matches && document.documentElement.dataset.theme !== 'light'
  || document.documentElement.dataset.theme === 'dark';
const COLORS = dark ? __COLORS_DARK__ : __COLORS_LIGHT__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmtDay = d => new Date(d + 'T00:00:00').toLocaleDateString('en-SG', {day: 'numeric', month: 'short'});
const legend = document.getElementById('legend');

if (!DATA.days.length || typeof Chart === 'undefined') {
  document.querySelector('.chart-wrap').innerHTML = '<p class="empty">No data yet: the chart fills in after the first daily run.</p>';
} else {
  const datasets = [
    {label: 'Typical range (low)', data: DATA.bandLow, borderWidth: 0, pointRadius: 0, fill: false, spanGaps: true},
    {label: "Google's typical range", data: DATA.bandHigh, borderWidth: 0, pointRadius: 0,
     backgroundColor: css('--band'), fill: '-1', spanGaps: true},
  ];
  DATA.airlines.forEach((a, i) => {
    if (!a.prices.some(p => p != null)) return;
    datasets.push({label: a.name, data: a.prices, borderColor: COLORS[i], backgroundColor: COLORS[i],
      borderWidth: 2, pointRadius: DATA.days.length < 20 ? 3 : 0, pointHoverRadius: 5, tension: 0.2, spanGaps: true});
    legend.insertAdjacentHTML('beforeend', `<li><i style="background:${COLORS[i]}"></i>${a.name}</li>`);
  });
  if (DATA.alerts.length) {
    const idx = Object.fromEntries(DATA.airlines.map((a, i) => [a.code, i]));
    datasets.push({label: 'Alert sent', type: 'scatter', pointStyle: 'triangle', pointRadius: 7,
      pointBorderColor: css('--surface'), pointBorderWidth: 2,
      pointBackgroundColor: DATA.alerts.map(a => COLORS[idx[a.airline]] || css('--text-2')),
      data: DATA.alerts.map(a => ({x: a.day, y: a.price, airline: a.airline}))});
    legend.insertAdjacentHTML('beforeend', '<li><i class="tri"></i>Alert sent</li>');
  }

  new Chart(document.getElementById('chart'), {
    type: 'line',
    data: {labels: DATA.days, datasets},
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: {mode: 'index', intersect: false},
      plugins: {
        legend: {display: false},
        tooltip: {
          filter: item => item.dataset.label !== 'Typical range (low)' && item.parsed.y != null,
          callbacks: {
            title: items => fmtDay(items[0].label),
            label: item => item.dataset.label === "Google's typical range"
              ? `Typical: S$${DATA.bandLow[item.dataIndex]}–${item.parsed.y}`
              : `${item.dataset.label}: S$${Math.round(item.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {ticks: {color: css('--text-2'), maxTicksLimit: 6, callback: function(v) { return fmtDay(this.getLabelForValue(v)); }},
            grid: {display: false}, border: {color: css('--grid')}},
        y: {ticks: {color: css('--text-2'), callback: v => 'S$' + v, maxTicksLimit: 6},
            grid: {color: css('--grid')}, border: {display: false}},
      },
    },
  });
}
</script>
</body>
</html>
"""
