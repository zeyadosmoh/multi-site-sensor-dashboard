"""Static SVG charts for the PDFs. Chart text is limited to numbers, dates, units and site codes; titles and
legends are written in the HTML around the chart, so the Arabic PDF needs no Arabic shaping inside matplotlib."""

import io
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

INK, MUTED, GRID = "#1f2933", "#6b7785", "#e3e8ee"
BLUE, ORANGE, RED, GREEN, AMBER = "#2f6fb0", "#d9822b", "#c23b3b", "#2e8b57", "#d4a017"
plt.rcParams.update({"font.size": 8, "font.family": "DejaVu Sans", "axes.edgecolor": GRID,
                     "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
                     "svg.fonttype": "path", "svg.hashsalt": "p7"})


def _svg(fig) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)
    s = buf.getvalue()
    return s[s.index("<svg"):]


def _axes(w=7.2, h=2.4):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    return fig, ax


def _days_axis(ax, days) -> None:
    if days:
        ax.set_xlim(days[0], days[-1])
    ax.xaxis.set_major_locator(mdates.DayLocator(bymonthday=(1, 5, 10, 15, 20, 25, 30)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d"))


def daily_temperature(daily: list[dict], t_min: float, t_max: float) -> str:
    days = [date.fromisoformat(d["day"]) for d in daily]
    fig, ax = _axes()
    lo = [d["temp_min"] for d in daily]
    hi = [d["temp_max"] for d in daily]
    ok = [i for i, v in enumerate(lo) if v is not None]
    ax.fill_between([days[i] for i in ok], [lo[i] for i in ok], [hi[i] for i in ok], color=BLUE, alpha=0.15,
                    linewidth=0)
    ax.plot(days, [d["temp_avg"] for d in daily], color=BLUE, linewidth=1.4)
    for v in (t_min, t_max):
        ax.axhline(v, color=RED, linewidth=0.8, linestyle="--")
    ax.set_ylabel("°C")
    _days_axis(ax, days)
    return _svg(fig)


def daily_humidity(daily: list[dict], h_min: float, h_max: float) -> str:
    days = [date.fromisoformat(d["day"]) for d in daily]
    fig, ax = _axes(h=2.0)
    ax.plot(days, [d["hum_avg"] for d in daily], color=ORANGE, linewidth=1.4)
    for v in (h_min, h_max):
        ax.axhline(v, color=RED, linewidth=0.8, linestyle="--")
    ax.set_ylabel("%")
    _days_axis(ax, days)
    return _svg(fig)


def hour_profile(profile: list[dict]) -> str:
    fig, ax = _axes(h=2.0)
    hours = [p["hour"] for p in profile]
    ax.plot(hours, [p["temp_avg"] for p in profile], color=BLUE, linewidth=1.4, marker="o", markersize=2.5)
    ax.set_ylabel("°C", color=BLUE)
    ax2 = ax.twinx()
    ax2.plot(hours, [p["hum_avg"] for p in profile], color=ORANGE, linewidth=1.2, linestyle="--")
    ax2.set_ylabel("%", color=ORANGE)
    ax2.spines[["top"]].set_visible(False)
    ax2.spines["right"].set_color(GRID)
    ax.set_xticks(range(0, 24, 3), [f"{h:02d}:00" for h in range(0, 24, 3)])
    return _svg(fig)


def sites_bars(sites: list[dict]) -> str:
    """Average temperature (bars, coloured by status) and hours out of range (dots, right axis) per site."""
    ordered = sorted(sites, key=lambda s: s["code"])
    codes = [s["code"] for s in ordered]
    colour = {"green": GREEN, "amber": AMBER, "red": RED}
    fig, ax = _axes(h=2.6)
    temps = [float(s["temp_avg"].replace(",", "")) if s["temp_avg"] != "–" else 0 for s in ordered]
    ax.bar(codes, temps, color=[colour[s["status"]] for s in ordered], alpha=0.85)
    ax.set_ylabel("°C")
    ax.tick_params(axis="x", rotation=45)
    ax2 = ax.twinx()
    ax2.plot(codes, [float(s["oor_hours"].replace(",", "")) for s in ordered], "o", color=INK, markersize=4)
    ax2.set_ylabel("h", color=INK)
    ax2.spines[["top"]].set_visible(False)
    return _svg(fig)
