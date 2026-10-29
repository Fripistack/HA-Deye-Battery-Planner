"""Autolader via evcc: kies de goedkoopste kwartieren tot vertrek.

Geen Home Assistant-afhankelijkheden, zodat de logica los te testen is.

Principe
--------
* Benodigde energie = (doel-SOC − huidige SOC) × accucapaciteit auto ÷ laadrendement.
* Uit alle kwartieren tot het vertrekmoment worden de goedkoopste gekozen, net zoveel
  als nodig is om die energie te laden. Bij gelijke prijs gaan kwartieren met de meeste
  verwachte zon voor.
* In een gekozen kwartier zet de planner evcc op "laden" (standaard modus *now*),
  anders op "wachten" (standaard *pv*: alleen laden op zonne-overschot).
* Noodlading: onder de nood-SOC wordt altijd direct geladen.
* De geplande autolading telt mee in het accuplan, zodat de thuisaccu niet leegloopt
  in de auto als dat niet loont.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

LADEN = "laden"
WACHTEN = "wachten"
NIET_VERBONDEN = "niet_verbonden"


@dataclass
class EvInstellingen:
    capaciteit_kwh: float = 60.0
    laadvermogen_w: float = 11000.0
    rendement: float = 90.0
    doel_soc: float = 80.0
    vertrek: str = "07:30:00"
    noodlaad_soc: float = 20.0


@dataclass
class EvPlan:
    stand: str
    reden: str
    nodig_kwh: float
    vertrek: datetime | None
    kwartieren: list[int]  # indexen van slots waarin geladen wordt
    per_slot_kwh: list[float]  # geplande lading per slot (netzijde)


def volgende_vertrek(nu: datetime, vertrek: str) -> datetime:
    try:
        t = time.fromisoformat(vertrek)
    except ValueError:
        t = time(7, 30)
    kandidaat = nu.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    if kandidaat <= nu:
        kandidaat += timedelta(days=1)
    return kandidaat


def plan(
    inst: EvInstellingen,
    nu: datetime,
    slot_starts: list[datetime],
    slot_fracs: list[float],
    prijzen: list[float],
    pv_overschot: list[float] | None,
    verbonden: bool | None,
    ev_soc: float | None,
    doel_soc: float | None = None,
) -> EvPlan:
    n = len(slot_starts)
    leeg = [0.0] * n
    if not verbonden:
        return EvPlan(NIET_VERBONDEN, "Car not connected", 0.0, None, [], leeg)

    doel = inst.doel_soc if doel_soc is None else doel_soc
    vertrek = volgende_vertrek(nu, inst.vertrek)
    kwartier_kwh = inst.laadvermogen_w / 1000.0 * 0.25
    eta = max(0.5, inst.rendement / 100.0)

    if ev_soc is None:
        return EvPlan(WACHTEN, "Car SOC unknown – charging on solar surplus only", 0.0, vertrek, [], leeg)

    nodig = max(0.0, (doel - ev_soc) / 100.0 * inst.capaciteit_kwh / eta)
    if nodig < 0.1:
        return EvPlan(WACHTEN, f"Car at target ({ev_soc:.0f}% ≥ {doel:.0f}%)", 0.0, vertrek, [], leeg)

    if ev_soc < inst.noodlaad_soc:
        per = leeg[:]
        if n:
            per[0] = kwartier_kwh * slot_fracs[0]
        return EvPlan(LADEN, f"Car below emergency SOC ({ev_soc:.0f}% < {inst.noodlaad_soc:.0f}%)",
                      round(nodig, 1), vertrek, [0] if n else [], per)

    kandidaten = [i for i in range(n) if slot_starts[i] < vertrek]
    pv = pv_overschot or [0.0] * n
    kandidaten.sort(key=lambda i: (prijzen[i], -pv[i], slot_starts[i]))

    gekozen: list[int] = []
    rest = nodig
    for i in kandidaten:
        if rest <= 1e-6:
            break
        gekozen.append(i)
        rest -= kwartier_kwh * slot_fracs[i]
    per = leeg[:]
    rest = nodig
    for i in sorted(gekozen):
        kwh = min(kwartier_kwh * slot_fracs[i], rest)
        per[i] = kwh
        rest -= kwh

    tekort = max(0.0, rest)
    if 0 in gekozen:
        reden = f"Cheap quarter before departure {vertrek:%a %H:%M} – {nodig:.1f} kWh needed"
        stand = LADEN
    elif not kandidaten:
        reden = "No prices until departure – charging on solar surplus only"
        stand = WACHTEN
    else:
        eerste = min(gekozen) if gekozen else None
        wanneer = f" next at {slot_starts[eerste]:%H:%M}" if eerste is not None else ""
        reden = f"Waiting for cheaper quarters –{wanneer}, {len(gekozen)} quarters planned"
        stand = WACHTEN
    if tekort > 0.1:
        reden += f" (not enough time: {tekort:.1f} kWh short)"
    return EvPlan(stand, reden, round(nodig, 1), vertrek, sorted(gekozen), per)

