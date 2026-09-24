"""Render public charts from anonymous aggregates only; never read source repositories.

Run: uv run --no-project --with matplotlib==3.11.2 python scripts/render_metrics.py
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import FuncFormatter, MaxNLocator


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "metrics"
INK = "#213c32"
MUTED = "#65786f"
GREEN = "#257854"
TEAL = "#187d8a"
PURPLE = "#755a9e"
ORANGE = "#b77732"
PAPER = "#f7faf7"
GRID = "#e0e8e2"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep*"]


def compact(value, _position=None):
    return f"{value / 1000:g}k" if abs(value) >= 1000 else f"{value:g}"


def save(fig, name):
    fig.savefig(OUTPUT / (name + ".png"), dpi=150, facecolor=fig.get_facecolor())
    fig.savefig(
        OUTPUT / (name + ".svg"),
        facecolor=fig.get_facecolor(),
        metadata={"Date": None, "Creator": "TODO Flow aggregate chart renderer"},
    )
    plt.close(fig)


def style_axis(ax):
    ax.set_facecolor("white")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="both", length=0, pad=8, labelcolor=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
    ax.yaxis.set_major_formatter(FuncFormatter(compact))
    ax.yaxis.set_major_locator(MaxNLocator(4))
    ax.set_xlim(-0.25, 8.55)
    ax.set_xticks(range(9), MONTHS)


def overview(rows):
    fig = plt.figure(figsize=(14, 10.2), facecolor=PAPER)
    fig.text(
        0.06, 0.952, "TODO FLOW  /  OBSERVED WORKFLOW HISTORY", color=GREEN, size=10, weight="bold"
    )
    fig.text(0.06, 0.902, "How recorded output changed", color=INK, size=27, weight="bold")
    fig.text(
        0.06,
        0.868,
        "January–September 2026 · monthly daily averages · anonymized repository aggregates",
        color=MUTED,
        size=11,
    )

    cards = [
        ("commits_per_calendar_day", 5, "Commit activity / day", "Jun → Sep", TEAL),
        ("source_churn_per_observed_day", 5, "Adjusted source changes / day", "Jun → Sep", GREEN),
        (
            "completed_increments_per_calendar_day",
            6,
            "Completed-status increments / day",
            "Jul → Sep",
            ORANGE,
        ),
    ]
    for index, (key, baseline, label, period, color) in enumerate(cards):
        x = 0.06 + index * 0.303
        fig.add_artist(
            FancyBboxPatch(
                (x, 0.728),
                0.274,
                0.105,
                boxstyle="round,pad=0.01,rounding_size=0.008",
                transform=fig.transFigure,
                facecolor="white",
                edgecolor=GRID,
                linewidth=0.8,
            )
        )
        ratio = rows[-1]["rates"][key] / rows[baseline]["rates"][key]
        fig.text(x + 0.011, 0.778, f"{ratio:.2f}×", size=25, color=color, weight="bold")
        fig.text(x + 0.12, 0.788, period, size=11, color=MUTED)
        fig.text(x + 0.011, 0.742, label, size=10, color=INK)

    panels = [
        (
            "commits_per_calendar_day",
            "Commit activity",
            "All reachable refs · includes merges",
            TEAL,
        ),
        (
            "source_churn_per_observed_day",
            "Source changes",
            "Adjusted additions + deletions · source-only",
            GREEN,
        ),
        (
            "merge_commits_per_calendar_day",
            "Integration merge commits",
            "First-parent merges · not verified PR counts",
            PURPLE,
        ),
        (
            "completed_increments_per_calendar_day",
            "Completed-status increments",
            "Positive net ledger changes · not unique features",
            ORANGE,
        ),
    ]
    axes = fig.subplots(2, 2)
    fig.subplots_adjust(left=0.075, right=0.95, bottom=0.15, top=0.648, hspace=0.73, wspace=0.2)
    for ax, (key, title, subtitle, color) in zip(axes.flat, panels):
        style_axis(ax)
        values = [row["rates"][key] for row in rows]
        xs = [i for i, value in enumerate(values) if value is not None]
        ys = [value for value in values if value is not None]
        ax.axvspan(5.5, 8.55, color="#edf5ef", zorder=0)
        ax.axvline(5.5, color="#b5c8bb", linestyle=(0, (3, 3)), linewidth=1)
        ax.plot(xs, ys, color=color, linewidth=2.3, marker="o", markersize=4.8, zorder=3)
        ax.scatter([8], [ys[-1]], s=50, facecolor="white", edgecolor=color, linewidth=2, zorder=4)
        ax.set_ylim(0, max(ys) * 1.3)
        ax.set_title(title, loc="left", size=13, color=INK, weight="bold", pad=32)
        ax.text(0, 1.08, subtitle, transform=ax.transAxes, color=MUTED, size=8.5)
        for i in sorted({xs[0], 5 if 5 in xs else xs[0], 8}):
            value = values[i]
            label = f"{value / 1000:.1f}k" if value >= 1000 else f"{value:.1f}"
            ax.annotate(
                label,
                (i, value),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                color=color,
                size=10,
                weight="bold",
            )
        if key == "completed_increments_per_calendar_day":
            ax.text(
                0.1,
                0.4,
                "No comparable ledger\nJan–Jun: unavailable, not zero",
                transform=ax.transAxes,
                size=9,
                color=MUTED,
            )
            ax.annotate(
                f"Aug {values[7]:.1f}",
                (7, ys[-2]),
                xytext=(-14, 10),
                textcoords="offset points",
                color=MUTED,
                size=9,
            )

    fig.text(
        0.075,
        0.1,
        "Shaded interval: registration / execution tooling first recorded in July; first use may be earlier.",
        color=MUTED,
        size=9,
    )
    fig.text(
        0.075,
        0.073,
        "*September covers days 1–23. Source rates exclude one documented import/relocation day in Jan and Jul.",
        color=MUTED,
        size=9,
    )
    fig.text(
        0.075,
        0.046,
        "Observational history, not a benchmark of this package or a causal estimate of labor productivity.",
        color=INK,
        size=9,
    )
    save(fig, "workflow-growth")


def source_changes(rows):
    fig, ax = plt.subplots(figsize=(14, 7.3), facecolor=PAPER)
    fig.subplots_adjust(left=0.075, right=0.95, bottom=0.21, top=0.7)
    fig.text(0.06, 0.932, "TODO FLOW  /  SOURCE CHANGE AUDIT", color=GREEN, size=10, weight="bold")
    fig.text(0.06, 0.86, "Added, deleted, and excluded", color=INK, size=26, weight="bold")
    fig.text(
        0.06,
        0.813,
        "Monthly totals · integration-branch non-merge history · source changes are not net code growth",
        color=MUTED,
        size=11,
    )
    style_axis(ax)
    adds = [r["source_add"] for r in rows]
    deletes = [r["source_delete"] for r in rows]
    totals = [r["source_churn"] for r in rows]
    excluded = [r["raw_code_add"] + r["raw_code_delete"] - r["source_churn"] for r in rows]
    ax.bar(range(9), adds, width=0.6, color=GREEN, label="Source additions", zorder=2)
    ax.bar(
        range(9), deletes, bottom=adds, width=0.6, color=ORANGE, label="Source deletions", zorder=2
    )
    ax.bar(
        range(9),
        excluded,
        bottom=totals,
        width=0.6,
        color="#e0e5e0",
        label="Excluded from adjusted source metric",
        zorder=2,
    )
    ax.set_xlim(-0.6, 8.6)
    ax.set_ylabel("Changed lines", color=MUTED, labelpad=10)
    ax.set_ylim(0, max(r["raw_code_add"] + r["raw_code_delete"] for r in rows) * 1.16)
    for i, total in enumerate(totals):
        top = rows[i]["raw_code_add"] + rows[i]["raw_code_delete"]
        ax.text(
            i, top + 12000, f"{total / 1000:.1f}k", ha="center", color=INK, size=9, weight="bold"
        )
    ax.legend(loc="upper left", bbox_to_anchor=(0, 1.13), ncol=3, frameon=False, fontsize=9)
    fig.text(
        0.075,
        0.13,
        "Labels show adjusted totals. Gray includes generated/vendor files, non-source formats, and excluded dates.",
        color=MUTED,
        size=10,
    )
    fig.text(
        0.075,
        0.09,
        "Jan 05: initial import excluded. Jul 02: cross-repository relocation excluded. Full-day exclusions are conservative.",
        color=MUTED,
        size=9,
    )
    fig.text(
        0.075,
        0.052,
        "September is partial (1–23). Binary changes are not counted as lines. See the adjacent methodology and numeric data.",
        color=MUTED,
        size=9,
    )
    save(fig, "source-changes")


def main():
    data = json.loads((OUTPUT / "measurements.json").read_text())
    rows = data["monthly"]
    assert [r["month"] for r in rows] == [f"2026-{m:02d}" for m in range(1, 10)]
    for row in rows:
        assert row["source_churn"] == row["source_add"] + row["source_delete"]
        assert (
            row["source_churn"] + sum(row["source_excluded"].values())
            == row["raw_code_add"] + row["raw_code_delete"]
        )
    plt.rcParams.update(
        {"font.family": "DejaVu Sans", "svg.fonttype": "path", "axes.unicode_minus": False}
    )
    overview(rows)
    source_changes(rows)
    print("Rendered anonymous workflow-growth and source-changes charts (PNG and SVG).")


if __name__ == "__main__":
    main()
