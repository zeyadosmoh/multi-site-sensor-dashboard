"""Hourly indoor temperature/humidity series per site (SYNTHETIC).

Indoor = set point + leak x (outdoor - set point). Outdoor = seasonal curve (monthly means interpolated per day)
+ day/night cycle (peak 15:00) + a slow weather anomaly (AR(1) per day). The building lags outdoor by ~2 hours.
Real out-of-range events (an AC failure, a heat wave, a water leak) are planted on top and listed in EVENTS.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from p7.generate.sites import SITES, Site

START = datetime(2026, 5, 1)
END = datetime(2026, 11, 1)  # exclusive
HOURS = int((END - START).total_seconds() // 3600)

SET_POINT = {"warehouse": (21.0, 45.0), "branch": (22.0, 45.0)}  # (temperature, humidity)
LEAK = {"warehouse": (0.34, 0.55), "branch": (0.15, 0.25)}


@dataclass(frozen=True)
class Event:
    site: str
    kind: str
    start: datetime
    end: datetime  # inclusive, hourly
    note: str


EVENTS: tuple[Event, ...] = (
    Event("JED-01", "ac_failure", datetime(2026, 8, 12, 10), datetime(2026, 8, 14, 16),
          "Cooling failure: indoor temperature and humidity drift toward outdoor for ~2.5 days"),
    Event("HOF-01", "heat_wave", datetime(2026, 7, 20, 0), datetime(2026, 7, 22, 23),
          "Heat wave: outdoor +5 C for three days; afternoon readings exceed the warehouse maximum"),
    Event("RUH-03", "water_leak", datetime(2026, 9, 3, 9), datetime(2026, 9, 3, 17),
          "Water leak: humidity +30 points for 9 hours"),
)


def timestamps() -> list[datetime]:
    return [START + timedelta(hours=i) for i in range(HOURS)]


def _seasonal(site: Site) -> np.ndarray:
    """Daily mean outdoor temperature, linear between mid-month anchors."""
    days = HOURS // 24
    month_starts = [0, 31, 61, 92, 123, 153]  # May..Oct day offsets from May 1
    mids = [m + 15 for m in month_starts]
    return np.interp(np.arange(days), mids, site.t_out)


def _ar1(rng: np.random.Generator, n: int, phi: float, sd: float) -> np.ndarray:
    out = np.zeros(n)
    eps = rng.normal(0, sd * np.sqrt(1 - phi**2), n)
    for i in range(1, n):
        out[i] = phi * out[i - 1] + eps[i]
    return out


def simulate_site(site: Site, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    days = HOURS // 24
    hour = np.arange(HOURS) % 24
    day = np.arange(HOURS) // 24
    lagged = (hour - 2) % 24  # building thermal lag

    t_anom_daily = _ar1(rng, days + 1, 0.75, 1.3)
    rh_anom_daily = _ar1(rng, days + 1, 0.7, 6.0)
    # interpolate the daily anomaly to hours so it changes smoothly
    t_anom = np.interp(np.arange(HOURS) / 24, np.arange(days + 1), t_anom_daily)
    rh_anom = np.interp(np.arange(HOURS) / 24, np.arange(days + 1), rh_anom_daily)

    diurnal = np.cos(2 * np.pi * (lagged - 15) / 24)
    t_out = _seasonal(site)[day] + site.t_amp * diurnal + t_anom
    rh_out = np.clip(site.rh_out * (1 - 0.25 * diurnal) + rh_anom, 3, 98)

    sp_t, sp_h = SET_POINT[site.kind]
    leak_t, leak_h = LEAK[site.kind]

    for ev in EVENTS:
        if ev.site == site.code and ev.kind == "heat_wave":
            a, b = _index(ev.start), _index(ev.end) + 1
            t_out[a:b] += 5.0

    temp = sp_t + leak_t * (t_out - sp_t) + rng.normal(0, 0.25, HOURS)
    hum = sp_h + leak_h * (rh_out - sp_h) + rng.normal(0, 1.2, HOURS)

    for ev in EVENTS:
        if ev.site != site.code:
            continue
        a, b = _index(ev.start), _index(ev.end) + 1
        if ev.kind == "ac_failure":
            # exponential drift toward outdoor (tau 3 h), then 4 h recovery back to normal
            k = np.arange(b - a)
            w = 1 - np.exp(-(k + 1) / 3)
            temp[a:b] = temp[a:b] * (1 - w) + (t_out[a:b] + 1.0) * w  # roof solar gain, no cooling
            hum[a:b] = hum[a:b] * (1 - w) + (rh_out[a:b] * 0.95) * w
            last_t, last_h = temp[b - 1], hum[b - 1]
            for j in range(4):
                f = (j + 1) / 5
                temp[b + j] = last_t * (1 - f) + temp[b + j] * f
                hum[b + j] = last_h * (1 - f) + hum[b + j] * f
        elif ev.kind == "water_leak":
            hum[a:b] += 30.0

    return np.round(temp, 1), np.round(np.clip(hum, 1, 99), 1)


def _index(ts: datetime) -> int:
    return int((ts - START).total_seconds() // 3600)


def simulate_all(seed: int) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    rng = np.random.default_rng(seed)
    return {s.code: simulate_site(s, rng) for s in SITES}
