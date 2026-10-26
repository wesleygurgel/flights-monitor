"""Gráficos em SVG calculados no servidor (sem biblioteca JS)."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from . import fmt


def sparkline(values: list[float], cap: float | None, w: int = 300, h: int = 60) -> dict | None:
    if len(values) < 2:
        return None
    pool = values + ([cap] if cap else [])
    lo, hi = min(pool) - 40, max(pool) + 40
    y = lambda v: round(h - 3 - (v - lo) / (hi - lo) * (h - 6), 1)  # noqa: E731
    pts = " ".join(f"{round(i * w / (len(values) - 1), 1)},{y(v)}" for i, v in enumerate(values))
    return {"points": pts, "cap_y": y(cap) if cap else None, "w": w, "h": h}


@dataclass
class Tick:
    pos: float
    label: str


def history_chart(series: list[tuple[date, float]], ref: float | None, cap: float | None,
                  alerts: list[tuple[date, float]], days: int = 30) -> dict | None:
    """Linha do menor preço por dia, com preço normal, teto e marcadores de alerta."""
    if len(series) < 2:
        return None
    W, H, L, T, B, R = 1000, 300, 84, 16, 262, 980
    end = date.today()
    start = end - timedelta(days=days - 1)
    pool = [p for _, p in series] + [v for v in (ref, cap) if v] + [p for _, p in alerts]
    step = 100 if max(pool) - min(pool) < 1200 else 500
    lo = (min(pool) // step) * step - step / 2
    hi = (max(pool) // step + 1) * step + step / 2
    X = lambda d: round(L + (d - start).days * (R - L) / (days - 1), 1)  # noqa: E731
    Y = lambda v: round(B - (v - lo) / (hi - lo) * (B - T), 1)  # noqa: E731
    yt, v = [], (lo // step + 1) * step
    while v < hi:
        yt.append(Tick(Y(v), fmt.num(v)))
        v += step
    xt = [Tick(X(start + timedelta(days=i)), fmt.d_short(start + timedelta(days=i)))
          for i in range(0, days - 4, 7)]  # para antes de encostar no "hoje"
    xt.append(Tick(X(end), "hoje"))
    return {
        "points": " ".join(f"{X(d)},{Y(p)}" for d, p in series if d >= start),
        "y_ticks": yt, "x_ticks": xt,
        "ref_y": Y(ref) if ref else None, "cap_y": Y(cap) if cap else None,
        "alerts": [{"x": X(d), "y": Y(p)} for d, p in alerts if d >= start],
        "left": L, "right": R, "width": W, "label_y": H - 8,
    }


def price_calendar(prices: dict[date, float], first: date, last: date, cap: float | None) -> list[dict]:
    """Um bloco por mês do período, cada dia com um nível de cor (0 mais barato .. 4 mais caro)."""
    if not prices:
        return []
    above = sorted(p for p in prices.values() if not cap or p > cap)
    q = [above[int(len(above) * k / 4)] for k in (1, 2, 3)] if above else []

    def level(p: float) -> str:
        if cap and p <= cap:
            return "cap"
        return str(1 + sum(p > t for t in q))

    months, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        lead = calendar.monthrange(y, m)[0]  # segunda=0
        cells = [{"day": "", "price": "", "lvl": "blank"} for _ in range(lead)]
        for d in range(1, calendar.monthrange(y, m)[1] + 1):
            dt = date(y, m, d)
            p = prices.get(dt)
            if p is None or not (first <= dt <= last):
                cells.append({"day": d, "price": "", "lvl": "none"})
            else:
                cells.append({"day": d, "price": fmt.num(p), "lvl": level(p)})
        months.append({"name": f"{fmt.MESES[m - 1].capitalize()} de {y}", "cells": cells})
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return months
