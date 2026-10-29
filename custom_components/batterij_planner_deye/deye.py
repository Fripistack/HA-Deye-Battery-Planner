"""Vertaling van het plan naar Deye-instellingen (Solarman / ha-solarman).

Geen Home Assistant-afhankelijkheden, zodat de logica los te testen is.

Deye werkt met zes TOU-programma's die samen de hele dag beslaan. In plaats van tijden
te verschuiven, schrijft de planner in alle zes programma's dezelfde waarden. Welk
programma op dit moment actief is maakt dan niet uit, en de tijden blijven staan.

| modus        | Work Mode           | Program Charging | Program SOC         | Program Power        |
|--------------|---------------------|------------------|---------------------|----------------------|
| zelfverbruik | Zero Export To CT   | Disabled         | min SOC             | max ontlaadvermogen  |
| vasthouden   | Zero Export To CT   | Disabled         | SOC bij begin blok  | max ontlaadvermogen  |
| laden        | Zero Export To CT   | Grid             | doel-SOC (blokeinde)| max ontlaadvermogen  |
| ontladen     | Export First        | Disabled         | doel-SOC (blokeinde)| trap × max ontlaad   |

Bij laden bepaalt *Battery Grid Charging Current* het laadvermogen (trap × max ÷ spanning).
Bij twee parallelle omvormers wordt het vermogen gelijk over beide verdeeld.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Achtervoegsels van entity-ID's / namen zoals ha-solarman ze aanmaakt (deye_p3.yaml).
PATRONEN: dict[str, tuple[str, str]] = {
    # sleutel: (domein, regex op het object-ID)
    "programma_soc": ("number", r"program_?(\d)_soc$"),
    "programma_vermogen": ("number", r"program_?(\d)_power$"),
    "programma_laden": ("select|switch", r"program_?(\d)_(charging|grid_charg\w*|charge)$"),
    "werkmodus": ("select", r"(^|_)work_mode$"),
    "time_of_use": ("select|switch", r"(^|_)time_of_use$"),
    "solar_sell": ("switch", r"(^|_)(solar_sell|export_surplus)$"),
    "netlaadstroom": ("number", r"(^|_)battery_grid_charging_current$"),
}


def ontdek(entity_ids: list[str]) -> dict[str, list[str] | str]:
    """Zoek de Deye-entiteiten van één omvormer in een lijst entity-ID's."""
    gevonden: dict[str, list[tuple[int, str]]] = {k: [] for k in PATRONEN}
    for eid in entity_ids:
        domein, _, obj = eid.partition(".")
        for sleutel, (domeinen, patroon) in PATRONEN.items():
            if domein not in domeinen.split("|"):
                continue
            m = re.search(patroon, obj)
            if m:
                nr = int(m.group(1)) if sleutel.startswith("programma_") else 0
                gevonden[sleutel].append((nr, eid))
    uit: dict[str, list[str] | str] = {}
    for sleutel, lijst in gevonden.items():
        if not lijst:
            continue
        lijst.sort()
        if sleutel.startswith("programma_"):
            uit[sleutel] = [eid for _, eid in lijst]
        else:
            uit[sleutel] = lijst[0][1]
    return uit


@dataclass
class DeyeOpties:
    min_soc: float
    max_soc: float
    max_laad_w: float
    max_ontlaad_w: float
    spanning: float = 51.2
    modus_zelf: str = "Zero Export To CT"
    modus_ontladen: str = "Export First"
    laden_aan: str = "Grid"
    laden_uit: str = "Disabled"
    tou_aan: str = "Enabled"
    export_blokkeren: bool = True


@dataclass
class Doel:
    """Gewenste toestand per omvormer."""

    werkmodus: str
    laden: bool
    soc: int
    vermogen_w: int
    netlaadstroom_a: int | None
    solar_sell: bool


def doel(
    modus: str,
    fractie: float,
    soc_nu: float,
    soc_eind_blok: float | None,
    hold_soc: float | None,
    terugprijs: float | None,
    aantal: int,
    o: DeyeOpties,
) -> Doel:
    """Bereken wat er in elke omvormer moet staan (vermogens per omvormer)."""
    aantal = max(1, aantal)
    max_ontlaad = int(o.max_ontlaad_w / aantal)
    sell = not (o.export_blokkeren and terugprijs is not None and terugprijs < 0)
    min_soc = int(round(o.min_soc))
    if modus == "laden":
        eind = soc_eind_blok if soc_eind_blok is not None else o.max_soc
        # naar boven afronden op 5 % zodat het doel niet elke 5 minuten verspringt
        soc = int(min(o.max_soc, math.ceil(eind / 5.0) * 5))
        stroom = int(fractie * o.max_laad_w / aantal / max(1.0, o.spanning))
        return Doel(o.modus_zelf, True, max(soc, min_soc), max_ontlaad, max(1, stroom), sell)
    if modus == "vasthouden":
        basis = hold_soc if hold_soc is not None else soc_nu
        soc = int(min(o.max_soc, max(o.min_soc, round(basis))))
        return Doel(o.modus_zelf, False, soc, max_ontlaad, None, sell)
    if modus == "ontladen":
        eind = soc_eind_blok if soc_eind_blok is not None else o.min_soc
        soc = int(max(o.min_soc, math.floor(eind / 5.0) * 5))
        vermogen = int(fractie * o.max_ontlaad_w / aantal)
        # ontladen naar het net heeft alleen zin als teruglevering iets oplevert
        return Doel(o.modus_ontladen, False, soc, max(100, vermogen), None, True)
    return Doel(o.modus_zelf, False, min_soc, max_ontlaad, None, sell)


def kies_optie(gewenst: str, beschikbaar: list[str] | None) -> str | None:
    """Zoek de optie van een select, hoofdletterongevoelig. None als hij niet bestaat."""
    if not beschikbaar:
        return gewenst
    for opt in beschikbaar:
        if str(opt).strip().lower() == gewenst.strip().lower():
            return str(opt)
    return None
