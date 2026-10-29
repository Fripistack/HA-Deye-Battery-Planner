"""Rekenkern van Batterij Planner Deye (geen Home Assistant-afhankelijkheden).

Dynamic programming over de accu-inhoud. Per kwartier kiest de planner een modus en,
bij laden en ontladen, een vermogenstrap (33 %, 66 % of 100 % van het maximum):
  zelfverbruik : omvormer regelt zelf (PV-overschot -> accu, tekort -> accu)
  vasthouden   : accu staat stil (overschot -> net, tekort -> net)
  laden        : accu laden uit PV en/of net
  ontladen     : accu ontladen naar huis en net

De doelfunctie is altijd: zo laag mogelijke netto energiekosten over de bekende
prijshorizon. Tot en met 2026 (saldering) is teruglevering evenveel waard als afname,
dus levert de planner zoveel mogelijk terug in dure kwartieren ("terugverdienen").
Vanaf 2027 telt teruglevering tegen marktprijs + opslag en kiest de planner vanzelf
voor eigen verbruik en zo laag mogelijke kosten.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

MODI = ("zelfverbruik", "vasthouden", "laden", "ontladen")
CODES = ("Z", "V", "L", "O")
TRAPPEN = (1 / 3, 2 / 3, 1.0)
KWARTIER = timedelta(minutes=15)
DAGEN = ("ma", "di", "wo", "do", "vr", "za", "zo")


@dataclass
class Instellingen:
    """Alle instelbare parameters."""

    capaciteit_kwh: float = 20.0
    min_soc: float = 10.0
    max_soc: float = 100.0
    max_laad_w: float = 10000.0
    max_ontlaad_w: float = 10000.0
    rendement_laden: float = 95.0
    rendement_ontladen: float = 95.0
    slijtage_eur_kwh: float = 0.03
    max_net_w: float = 17000.0
    pv_factor: float = 0.9
    saldering: str = "auto"  # auto | aan | uit
    saldering_correctie: float = 0.0
    teruglever_opslag: float = 0.0182
    teruglever_factor: float = 1.0
    eindwaarde_factor: float = 1.0
    modus_drempel: float = 0.002
    verbruik_fallback_w: float = 500.0
    soc_stap_kwh: float = 0.1
    vermogenstrappen: bool = True
    export_blokkeren_negatief: bool = True


@dataclass
class Slot:
    """Eén kwartier in de planningshorizon."""

    start: datetime
    eind: datetime
    afname: float
    markt: float
    terug: float = 0.0
    pv: float = 0.0  # kWh in dit (deel)kwartier
    verbruik: float = 0.0  # kWh in dit (deel)kwartier, huis zonder auto
    ev: float = 0.0  # kWh geplande autolading in dit (deel)kwartier
    frac: float = 1.0  # deel van het kwartier dat nog resteert


def opties(trappen: bool) -> list[tuple[int, float]]:
    """(modus, vermogensfractie) waaruit de planner per kwartier kiest."""
    stappen = TRAPPEN if trappen else (1.0,)
    return [(0, 1.0), (1, 0.0)] + [(2, f) for f in stappen] + [(3, f) for f in stappen]


def strategie(moment: datetime, inst: Instellingen) -> str:
    sald = inst.saldering == "aan" or (inst.saldering == "auto" and moment.year < 2027)
    return "terugverdienen" if sald else "laagste_kosten"


def _to_dt(value: Any, tz: tzinfo) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def bouw_slots(prijsreeksen: list[list[dict]], nu: datetime, tz: tzinfo) -> list[Slot]:
    """Zet Frank-prijsblokken om naar toekomstige kwartieren (uurblokken worden gesplitst)."""
    per_start: dict[datetime, Slot] = {}
    for reeks in prijsreeksen:
        for blok in reeks or []:
            try:
                st = _to_dt(blok.get("from"), tz)
                en = _to_dt(blok.get("till"), tz)
                afname = float(blok.get("total_price_eur_kwh"))
                markt = float(blok.get("market_price"))
            except (TypeError, ValueError, AttributeError):
                continue
            if st is None or en is None:
                continue
            # in UTC rekenen zodat de zomer-/wintertijdovergang goed gaat
            s = st.astimezone(timezone.utc)
            e_u = en.astimezone(timezone.utc)
            while s < e_u:
                e = s + KWARTIER
                if e > nu:
                    sl = s.astimezone(tz)
                    per_start[s] = Slot(sl, e.astimezone(tz), afname, markt)
                s = e
    slots = [per_start[k] for k in sorted(per_start)]
    if slots:
        f = (slots[0].eind - nu).total_seconds() / 900.0
        slots[0].frac = max(0.05, min(1.0, f))
    return slots


def zet_terugleverprijs(slots: list[Slot], inst: Instellingen) -> None:
    for s in slots:
        sald = inst.saldering == "aan" or (inst.saldering == "auto" and s.start.year < 2027)
        if sald:
            s.terug = s.afname - inst.saldering_correctie
        else:
            s.terug = (s.markt + inst.teruglever_opslag) * inst.teruglever_factor


def zet_verbruik(slots: list[Slot], werkdag: list | None, weekend: list | None, fallback_w: float) -> None:
    for s in slots:
        prof = weekend if s.start.weekday() >= 5 else werkdag
        w = fallback_w
        if prof and len(prof) == 24:
            try:
                w = float(prof[s.start.hour])
            except (TypeError, ValueError):
                w = fallback_w
        s.verbruik = max(0.0, w) / 1000.0 * 0.25 * s.frac


def zet_pv_solcast(slots: list[Slot], items: list[dict], tz: tzinfo, factor: float) -> bool:
    """Solcast detailedForecast (pv_estimate in kW per 30 min). Geeft False als leeg."""
    kw: dict[datetime, float] = {}
    for it in items or []:
        try:
            ps = _to_dt(it.get("period_start"), tz)
            val = float(it.get("pv_estimate"))
        except (TypeError, ValueError, AttributeError):
            continue
        if ps is not None:
            kw[ps.replace(second=0, microsecond=0)] = val
    if not kw:
        return False
    for s in slots:
        key = s.start - timedelta(minutes=s.start.minute % 30)
        s.pv = kw.get(key, 0.0) * 0.25 * s.frac * factor
    return True


def zet_pv_verdeeld(
    slots: list[Slot],
    totalen: dict[date, float],
    zon: dict[date, tuple[datetime, datetime]],
    factor: float,
) -> None:
    """Verdeel dagtotalen (kWh) over de kwartieren met een zonnecurve."""
    gewicht: list[float] = []
    som: dict[date, float] = {}
    for s in slots:
        d = s.start.date()
        w = 0.0
        if d in zon:
            r, z = zon[d]
            lengte = (z - r).total_seconds()
            mid = s.start + timedelta(minutes=7.5)
            if lengte > 0 and r < mid < z:
                x = (mid - r).total_seconds() / lengte
                w = math.sin(math.pi * x) ** 1.5 * s.frac
        gewicht.append(w)
        som[d] = som.get(d, 0.0) + w
    for s, w in zip(slots, gewicht):
        d = s.start.date()
        if som.get(d, 0.0) > 0:
            s.pv = totalen.get(d, 0.0) * w / som[d] * factor
        else:
            s.pv = 0.0


def _hhmm(d: datetime) -> str:
    return f"{d.hour:02d}:{d.minute:02d}"


def zet_ev(slots: list[Slot], plan: list[float]) -> None:
    """Geplande autolading (kWh per kwartier, zelfde volgorde als slots)."""
    for s, kwh in zip(slots, plan):
        s.ev = max(0.0, kwh)


def optimaliseer(slots: list[Slot], soc_pct: float, inst: Instellingen) -> dict[str, Any]:
    """Bereken het optimale plan. Verwacht gevulde afname/terug/pv/verbruik per slot."""
    n = len(slots)
    if n == 0:
        raise ValueError("geen prijzen beschikbaar")

    cap = inst.capaciteit_kwh
    emin = cap * inst.min_soc / 100.0
    emax = cap * inst.max_soc / 100.0
    eta_c = inst.rendement_laden / 100.0
    eta_d = inst.rendement_ontladen / 100.0
    slijt = inst.slijtage_eur_kwh
    drempel = inst.modus_drempel
    keuzes = opties(bool(inst.vermogenstrappen))

    imp = [s.afname for s in slots]
    exp = [max(0.0, s.terug) if inst.export_blokkeren_negatief else s.terug for s in slots]
    pv = [s.pv for s in slots]
    ld = [s.verbruik + s.ev for s in slots]
    pc = [inst.max_laad_w / 1000.0 * 0.25 * s.frac for s in slots]
    pd = [inst.max_ontlaad_w / 1000.0 * 0.25 * s.frac for s in slots]
    net = [inst.max_net_w / 1000.0 * 0.25 * s.frac for s in slots]

    def stap(e: float, c: int, i: int) -> tuple[float, float, float, float]:
        m, f = keuzes[c]
        ov = pv[i] - ld[i]
        if m == 0:
            if ov >= 0:
                b = min(ov, pc[i], max(0.0, emax - e) / eta_c)
            else:
                b = -min(-ov, pd[i], max(0.0, e - emin) * eta_d)
        elif m == 1:
            b = 0.0
        elif m == 2:
            b = min(pc[i] * f, max(0.0, emax - e) / eta_c)
        else:
            b = -min(pd[i] * f, max(0.0, e - emin) * eta_d)
        e2 = e + b * eta_c if b >= 0 else e + b / eta_d
        g = ld[i] - pv[i] + b
        k = g * imp[i] if g >= 0 else g * exp[i]
        if b < 0:
            k -= b * slijt
        if m != 0:
            k += drempel
            # aansluitwaarde: geplande acties mogen de netaansluiting niet overbelasten
            if abs(g) > net[i] + 1e-9:
                k = math.inf
        return e2, k, b, g

    stapgrootte = max(inst.soc_stap_kwh, cap / 250.0)
    aantal = max(2, int(round((emax - emin) / stapgrootte)) + 1)
    dstap = (emax - emin) / (aantal - 1) if emax > emin else 1.0
    raster = [emin + j * dstap for j in range(aantal)]

    def interp(v: list[float], e: float) -> float:
        x = (e - emin) / dstap
        if x <= 0:
            return v[0]
        j = int(x)
        if j >= aantal - 1:
            return v[-1]
        fr = x - j
        return v[j] * (1.0 - fr) + v[j + 1] * fr

    # restwaarde: p75 van de laatste 24 uur, nooit hoger dan het goedkoopste
    # kwartier in de laatste 4 uur (anders gaat het plan aan het eind hamsteren)
    staart = sorted(imp[max(0, n - 96):])
    p75 = staart[int(len(staart) * 0.75)]
    laatste = min(imp[max(0, n - 16):])
    eindprijs = max(0.0, min(p75 * inst.eindwaarde_factor * eta_d - slijt, laatste))

    aantal_keuzes = len(keuzes)
    waarden: list[list[float]] = [[]] * (n + 1)
    waarden[n] = [-(e - emin) * eindprijs for e in raster]
    for i in range(n - 1, -1, -1):
        volgende = waarden[i + 1]
        rij = []
        for e in raster:
            best = math.inf
            for c in range(aantal_keuzes):
                r = stap(e, c, i)
                if r[1] == math.inf:
                    continue
                tot = r[1] + interp(volgende, r[0])
                if tot < best - 1e-9:
                    best = tot
            rij.append(best)
        waarden[i] = rij

    e0 = min(max(cap * soc_pct / 100.0, emin), emax)
    e = e0
    gekozen: list[int] = []
    soc: list[float] = []
    accu: list[float] = []
    kosten_plan = 0.0
    for i in range(n):
        best = math.inf
        bc, br = 0, None
        for c in range(aantal_keuzes):
            r = stap(e, c, i)
            if r[1] == math.inf:
                continue
            tot = r[1] + interp(waarden[i + 1], r[0])
            if tot < best - 1e-9:
                best, bc, br = tot, c, r
        if br is None:
            bc, br = 0, stap(e, 0, i)
        gekozen.append(bc)
        e = br[0]
        kosten_plan += br[1] - (drempel if keuzes[bc][0] else 0.0)
        soc.append(e)
        accu.append(br[2])
    kosten_plan -= (e - emin) * eindprijs

    e = e0
    kosten_ref = 0.0
    for i in range(n):
        r = stap(e, 0, i)
        e = r[0]
        kosten_ref += r[1]
    kosten_ref -= (e - emin) * eindprijs

    def pct(c: int) -> int:
        m, f = keuzes[c]
        return round(f * 100) if m in (2, 3) else 0

    blokken = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and gekozen[j + 1] == gekozen[i]:
            j += 1
        soc_start = (e0 if i == 0 else soc[i - 1]) / cap * 100.0
        blokken.append(
            {
                "start": slots[i].start.isoformat(),
                "eind": slots[j].eind.isoformat(),
                "tijd": f"{DAGEN[slots[i].start.weekday()]} {_hhmm(slots[i].start)}-{_hhmm(slots[j].eind)}",
                "modus": MODI[keuzes[gekozen[i]][0]],
                "vermogen_pct": pct(gekozen[i]),
                "soc_start": round(soc_start, 1),
                "soc_eind": round(soc[j] / cap * 100.0, 1),
                "afname_gem": round(sum(imp[i : j + 1]) / (j - i + 1), 4),
                "teruglever_gem": round(sum(exp[i : j + 1]) / (j - i + 1), 4),
                "auto_kwh": round(sum(s.ev for s in slots[i : j + 1]), 1),
            }
        )
        i = j + 1

    compact = [
        [
            int(s.start.timestamp() * 1000),
            CODES[keuzes[gekozen[k]][0]],
            round(soc[k] / cap * 100.0, 1),
            round(s.afname, 4),
            round(s.terug, 4),
            round(s.pv * 4 / s.frac, 2),
            round(s.verbruik * 4 / s.frac, 2),
            pct(gekozen[k]),
            round(s.ev * 4 / s.frac, 2),
        ]
        for k, s in enumerate(slots)
    ]

    j = 0
    while j + 1 < n and gekozen[j + 1] == gekozen[0]:
        j += 1

    return {
        "modus": MODI[keuzes[gekozen[0]][0]],
        "vermogen_pct": pct(gekozen[0]),
        "strategie": strategie(slots[0].start, inst),
        "blok_einde": slots[j].eind,
        "soc_nu": round(soc_pct, 1),
        "soc_doel_einde_blok": blokken[0]["soc_eind"],
        "kosten_plan": round(kosten_plan, 2),
        "kosten_zelfverbruik": round(kosten_ref, 2),
        "besparing": round(kosten_ref - kosten_plan, 2),
        "doorvoer_kwh": round(sum(abs(b) for b in accu), 1),
        "horizon_einde": slots[-1].eind,
        "kwartieren": n,
        "restwaarde_eur_kwh": round(eindprijs, 4),
        "pv_verwacht_kwh": round(sum(pv), 1),
        "verbruik_verwacht_kwh": round(sum(s.verbruik for s in slots), 1),
        "auto_gepland_kwh": round(sum(s.ev for s in slots), 1),
        "terugprijs_nu": round(slots[0].terug, 4),
        "afnameprijs_nu": round(slots[0].afname, 4),
        "blokken": blokken,
        "slots": compact,
    }
