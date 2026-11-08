#!/usr/bin/env python
"""
scripts/phase2_plot.py — the Phase 2 figure (run phase2_sim.py and phase2_backtest.py first).

Left:  MomentumTrader equity — SimSession test seeds 10–29 (gray) and their median
       (blue), and the excess over holding on the recorded weeks 3–6 (colours).
Right: look-ahead check — mean P&L BEFORE fees vs how late the feature is read,
       in SimSession and on the recorded sessions.

    python scripts/phase2_plot.py          → BACKTEST_equity.png
"""

from __future__ import annotations

import json
import pathlib
import re
import statistics as st

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "phase2"

SURFACE, INK, INK2, GRID, SEED = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#c9c8c3"
BLUE, ORANGE, AQUA, YELLOW, MAGENTA = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"
WEEK_COLOURS = {"week03": AQUA, "week04": YELLOW, "week05": ORANGE, "week06": MAGENTA}


def style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:+,.0f}"))
    ax.axhline(0, color=INK2, linewidth=0.8)


def delay_table(md: str) -> dict[int, float]:
    """{delay: mean P&L before fees} from a generated markdown table."""
    out = {}
    for line in md.splitlines():
        if m := re.match(r"\| delay (\d+) \|(?:[^|]*\|){4}\s*([+\-][\d,]+)", line):        # sim.md
            out[int(m[1])] = float(m[2].replace(",", ""))
        elif m := re.match(r"\| (\d+) \| [^|]+\| ([+\-][\d,]+) \|", line):               # recorded.md
            out[int(m[1])] = float(m[2].replace(",", ""))
    return out


def main() -> int:
    sim = json.loads((DATA / "sim_curves.json").read_text())
    rec = json.loads((DATA / "recorded_curves.json").read_text())
    look, entry = sim["chosen"]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2), gridspec_kw={"width_ratios": [1.6, 1]})
    fig.patch.set_facecolor(SURFACE)
    for ax in (a1, a2):
        style(ax)

    # left: equity
    for cv in sim["test"]:
        a1.plot([i * 25 / 60 for i in range(len(cv))], cv, color=SEED, linewidth=0.8, zorder=1)
    n = min(len(cv) for cv in sim["test"])
    med = [st.median(cv[i] for cv in sim["test"]) for i in range(n)]
    xs = [i * 25 / 60 for i in range(n)]
    a1.plot(xs, med, color=BLUE, linewidth=2, zorder=3,
            label=f"SimSession, median of {len(sim['test'])} test seeds")
    a1.annotate(f"SimSession median  {med[-1]:+,.0f}", (xs[-1], med[-1]), xytext=(4, 6),
                textcoords="offset points", fontsize=8, color=INK)
    finals = sorted((r["curve"][-1][1], r["tape"]) for r in rec
                    if r["strategy"] == "momentum" and not r.get("delay"))
    nudge = {tape: 9 * (i % 2 * 2 - 1) if i and abs(finals[i][0] - finals[i - 1][0]) < 400 else 0
             for i, (_, tape) in enumerate(finals)}               # pull close labels apart
    for r in rec:
        if r["strategy"] != "momentum" or r.get("delay"):
            continue
        t = [p[0] / 60 for p in r["curve"]]
        v = [p[1] for p in r["curve"]]
        wk = r["tape"].replace("week0", "week ")
        a1.plot(t, v, color=WEEK_COLOURS[r["tape"]], linewidth=1.6, zorder=4, label=f"recorded {wk} (excess vs hold)")
        a1.annotate(f"{wk}  {v[-1]:+,.0f}", (t[-1], v[-1]), xytext=(4, nudge[r["tape"]]), textcoords="offset points",
                    va="center", fontsize=8, color=INK)
    a1.set_xlim(0, 100)
    a1.set_xlabel("minutes (SimSession: 1 tick = 1 s)", color=INK2, fontsize=9)
    a1.set_ylabel("P&L after the 0.30 % fee, $", color=INK2, fontsize=9)
    a1.set_title(f"Momentum (lookback {look}, entry {entry:.1%}): loses in both markets",
                 loc="left", fontsize=10.5, color=INK)
    a1.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=3,
              labelcolor=INK)

    # right: look-ahead
    for name, file, col in (("SimSession, 20 test seeds", "sim.md", BLUE),
                            ("recorded weeks 3–6", "recorded.md", ORANGE)):
        d = delay_table((DATA / file).read_text())
        ks = sorted(d)
        a2.plot(range(len(ks)), [d[k] for k in ks], color=col, linewidth=2, marker="o", markersize=7,
                markeredgecolor=SURFACE, markeredgewidth=2, label=name)
        a2.annotate(f"{d[ks[0]]:+,.0f}", (0, d[ks[0]]), xytext=(-8, 0), textcoords="offset points",
                    ha="right", va="center", fontsize=8, color=INK)
        a2.annotate(f"{d[ks[-1]]:+,.0f}", (len(ks) - 1, d[ks[-1]]), xytext=(8, 0), textcoords="offset points",
                    ha="left", va="center", fontsize=8, color=INK)
    a2.set_xticks(range(4), ["0 (no delay)", "+1", "+2", "+5"])
    a2.set_xlim(-0.6, 3.6)
    a2.set_xlabel("feature read k ticks late", color=INK2, fontsize=9)
    a2.set_ylabel("mean P&L before fees, $", color=INK2, fontsize=9)
    a2.set_title("Look-ahead check", loc="left", fontsize=10.5, color=INK)
    a2.legend(frameon=False, fontsize=8, loc="lower right", labelcolor=INK)

    fig.tight_layout()
    out = ROOT / "BACKTEST_equity.png"
    fig.savefig(out, dpi=160, facecolor=SURFACE)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
