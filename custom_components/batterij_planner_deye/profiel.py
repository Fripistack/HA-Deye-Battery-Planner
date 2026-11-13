"""Zelflerend verbruiksprofiel en zelflerende PV-correctie, opgeslagen in .storage.

Wat er geleerd wordt (allemaal per woning, dus ook bruikbaar bij een andere accu of
een ander verbruik):

* verbruik per uur, apart voor werkdag/weekend én per weekdag (ma..zo). Het
  weekdagprofiel wordt gebruikt zodra een uur minstens ``MIN_PER_DAG`` metingen heeft.
  Omdat het een voortschrijdend gemiddelde is, schuift het vanzelf mee met het seizoen.
* verbruiksmarge: per dag de verhouding werkelijk / voorspeld verbruik. De marge is het
  80e percentiel van de laatste ``DAGEN_HISTORIE`` dagen (begrensd op 1,0 – 1,5): op
  ongeveer 4 van de 5 dagen is de geschatte benodigde energie dan genoeg.
* PV-zekerheid: per dag de verhouding werkelijke PV-opbrengst / voorspelling. De factor
  is het 25e percentiel (begrensd op 0,3 – 1,2): zoveel van de voorspelling komt er
  vrijwel altijd. Werkt alleen als er PV-vermogenssensoren zijn ingesteld.

De rekenfuncties onderaan hebben geen Home Assistant nodig en zijn los te testen.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

ALPHA_WERKDAG = 0.1
ALPHA_WEEKEND = 0.2
ALPHA_PER_DAG = 0.25
MIN_PER_DAG = 3
DAGEN_HISTORIE = 21
MARGE_MIN, MARGE_MAX, MARGE_STANDAARD = 1.0, 1.5, 1.1
PV_MIN, PV_MAX, PV_STANDAARD = 0.3, 1.2, 0.8
MIN_DAGEN_LEREN = 3


def percentiel(waarden: list[float], p: float) -> float:
    """Eenvoudig percentiel (lineair geïnterpoleerd), p tussen 0 en 1."""
    v = sorted(waarden)
    if not v:
        raise ValueError("lege lijst")
    x = (len(v) - 1) * p
    j = int(x)
    if j + 1 >= len(v):
        return v[-1]
    return v[j] + (v[j + 1] - v[j]) * (x - j)


def leer_marge(verhoudingen: list[float]) -> float:
    """Verbruiksmarge uit dagverhoudingen werkelijk/voorspeld."""
    if len(verhoudingen) < MIN_DAGEN_LEREN:
        return MARGE_STANDAARD
    return round(min(MARGE_MAX, max(MARGE_MIN, percentiel(verhoudingen, 0.8))), 3)


def leer_pv_factor(verhoudingen: list[float]) -> float:
    """PV-zekerheidsfactor uit dagverhoudingen werkelijk/voorspeld."""
    if len(verhoudingen) < MIN_DAGEN_LEREN:
        return PV_STANDAARD
    return round(min(PV_MAX, max(PV_MIN, percentiel(verhoudingen, 0.25))), 3)


def _voeg_toe(lijst: list[float], waarde: float) -> list[float]:
    return (lijst + [round(waarde, 3)])[-DAGEN_HISTORIE:]


class _Integrator:
    """Tijdgewogen integraal van een vermogen (W)."""

    def __init__(self) -> None:
        self.integraal = 0.0  # W·s
        self.duur = 0.0
        self._w: float | None = None
        self._t: datetime | None = None

    def meting(self, watt: float | None, t: datetime) -> None:
        if self._w is not None and self._t is not None:
            dt = (t - self._t).total_seconds()
            if 0 < dt < 3600:
                self.integraal += self._w * dt
                self.duur += dt
        self._w = watt
        self._t = t

    def reset(self) -> None:
        self.integraal = 0.0
        self.duur = 0.0


class VerbruiksProfiel:
    """Tijdgewogen kwartiergemiddelde -> exponentieel voortschrijdend gemiddelde per uur."""

    def __init__(self, hass: HomeAssistant, entry_id: str, start_w: float) -> None:
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry_id}.profiel")
        self.werkdag: list[float] = [start_w] * 24
        self.weekend: list[float] = [start_w] * 24
        self.n_werkdag: list[int] = [0] * 24
        self.n_weekend: list[int] = [0] * 24
        self.per_dag: list[list[float]] = [[start_w] * 24 for _ in range(7)]
        self.n_per_dag: list[list[int]] = [[0] * 24 for _ in range(7)]
        # zelflerende marge (verbruik) en PV-zekerheid
        self.verbruik_verhoudingen: list[float] = []
        self.pv_verhoudingen: list[float] = []
        self.laatste_kwartier_w: float | None = None
        self._dag: str | None = None
        self._dag_werkelijk = 0.0  # kWh
        self._dag_voorspeld = 0.0  # kWh
        self._dag_dekking = 0.0  # gemeten deel van de dag (uren)
        self._pv_dag: str | None = None
        self._pv_voorspeld: float | None = None  # kWh, hele dag, zonder pv_factor
        self._pv_werkelijk = _Integrator()
        self._verbruik = _Integrator()

    # ------------------------------------------------------------ opslag
    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        for naam in ("werkdag", "weekend", "n_werkdag", "n_weekend"):
            waarde = data.get(naam)
            if isinstance(waarde, list) and len(waarde) == 24:
                setattr(self, naam, waarde)
        for naam in ("per_dag", "n_per_dag"):
            waarde = data.get(naam)
            if isinstance(waarde, list) and len(waarde) == 7 and all(len(r) == 24 for r in waarde):
                setattr(self, naam, waarde)
        if data.get("per_dag") is None:
            # eerste keer na update: weekdagprofiel starten vanuit werkdag/weekend
            self.per_dag = [list(self.weekend if d >= 5 else self.werkdag) for d in range(7)]
        for naam in ("verbruik_verhoudingen", "pv_verhoudingen"):
            waarde = data.get(naam)
            if isinstance(waarde, list):
                setattr(self, naam, [float(x) for x in waarde][-DAGEN_HISTORIE:])
        for naam in ("_dag", "_pv_dag"):
            if isinstance(data.get(naam.lstrip("_")), str):
                setattr(self, naam, data[naam.lstrip("_")])
        for naam in ("dag_werkelijk", "dag_voorspeld", "dag_dekking"):
            try:
                setattr(self, "_" + naam, float(data.get(naam) or 0.0))
            except (TypeError, ValueError):
                pass
        pv = data.get("pv_voorspeld")
        self._pv_voorspeld = float(pv) if isinstance(pv, (int, float)) else None
        try:
            self._pv_werkelijk.integraal = float(data.get("pv_werkelijk_ws") or 0.0)
            self._pv_werkelijk.duur = float(data.get("pv_werkelijk_s") or 0.0)
        except (TypeError, ValueError):
            pass

    def _data(self) -> dict[str, Any]:
        return {
            "werkdag": self.werkdag,
            "weekend": self.weekend,
            "n_werkdag": self.n_werkdag,
            "n_weekend": self.n_weekend,
            "per_dag": self.per_dag,
            "n_per_dag": self.n_per_dag,
            "verbruik_verhoudingen": self.verbruik_verhoudingen,
            "pv_verhoudingen": self.pv_verhoudingen,
            "dag": self._dag,
            "dag_werkelijk": round(self._dag_werkelijk, 4),
            "dag_voorspeld": round(self._dag_voorspeld, 4),
            "dag_dekking": round(self._dag_dekking, 3),
            "pv_dag": self._pv_dag,
            "pv_voorspeld": self._pv_voorspeld,
            "pv_werkelijk_ws": round(self._pv_werkelijk.integraal, 1),
            "pv_werkelijk_s": round(self._pv_werkelijk.duur, 1),
        }

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    # ------------------------------------------------------------ voorspelling
    def voorspel_w(self, weekdag: int, uur: int) -> float:
        """Verwacht verbruik (W): weekdagprofiel als dat genoeg metingen heeft."""
        if self.n_per_dag[weekdag][uur] >= MIN_PER_DAG:
            return float(self.per_dag[weekdag][uur])
        prof = self.weekend if weekdag >= 5 else self.werkdag
        return float(prof[uur])

    def profiel_per_dag(self) -> list[list[float]]:
        """7 × 24 verwachtingen (W) voor de planner."""
        return [[self.voorspel_w(d, u) for u in range(24)] for d in range(7)]

    @property
    def verbruik_marge(self) -> float:
        return leer_marge(self.verbruik_verhoudingen)

    @property
    def pv_zeker_factor(self) -> float:
        return leer_pv_factor(self.pv_verhoudingen)

    # ------------------------------------------------------------ verbruik meten
    def meting(self, watt: float | None, t: datetime) -> None:
        """Nieuwe waarde van de verbruikssensor (of None bij onbekend)."""
        self._verbruik.meting(watt, t)

    def afsluiten(self, t: datetime) -> None:
        """Aan het eind van elk kwartier: gemiddelde verwerken in het profiel."""
        self._verbruik.meting(self._verbruik._w, t)
        moment = t - timedelta(minutes=1)
        self._dag_wissel(moment.date())
        if self._verbruik.duur >= 300:
            gem = max(0.0, self._verbruik.integraal / self._verbruik.duur)
            self.laatste_kwartier_w = round(gem, 0)
            uur = moment.hour
            dag = moment.weekday()
            # voorspelling vóór het bijwerken, voor het leren van de marge
            self._dag_voorspeld += self.voorspel_w(dag, uur) * 0.25 / 1000.0
            self._dag_werkelijk += gem * 0.25 / 1000.0
            self._dag_dekking += 0.25
            if dag >= 5:
                prof, tel, basis = self.weekend, self.n_weekend, ALPHA_WEEKEND
            else:
                prof, tel, basis = self.werkdag, self.n_werkdag, ALPHA_WERKDAG
            # eerste metingen tellen zwaar zodat het profiel snel bruikbaar is
            alpha = max(basis, 1.0 / (tel[uur] + 1))
            prof[uur] = round(prof[uur] * (1 - alpha) + gem * alpha, 1)
            tel[uur] = min(tel[uur] + 1, 1000)
            pd, nd = self.per_dag[dag], self.n_per_dag[dag]
            alpha = max(ALPHA_PER_DAG, 1.0 / (nd[uur] + 1))
            pd[uur] = round(pd[uur] * (1 - alpha) + gem * alpha, 1)
            nd[uur] = min(nd[uur] + 1, 1000)
            self._store.async_delay_save(self._data, 120)
        self._verbruik.reset()

    def _dag_wissel(self, d: date) -> None:
        dag = d.isoformat()
        if self._dag == dag:
            return
        # vorige dag afronden: alleen als hij grotendeels gemeten is
        if self._dag is not None and self._dag_dekking >= 20 and self._dag_voorspeld > 0.5:
            self.verbruik_verhoudingen = _voeg_toe(
                self.verbruik_verhoudingen, self._dag_werkelijk / self._dag_voorspeld
            )
        self._dag = dag
        self._dag_werkelijk = self._dag_voorspeld = self._dag_dekking = 0.0

    # ------------------------------------------------------------ PV leren
    def pv_meting(self, watt: float | None, t: datetime) -> None:
        self._pv_werkelijk.meting(watt, t)

    def pv_voorspelling(self, d: date, kwh: float, hele_dag: bool) -> None:
        """Dagvoorspelling vastleggen (één keer per dag, alleen als hij de hele dag dekt)."""
        dag = d.isoformat()
        if self._pv_dag == dag:
            return
        self._pv_dag_afsluiten()
        self._pv_dag = dag
        self._pv_voorspeld = kwh if hele_dag else None
        self._pv_werkelijk.reset()

    def _pv_dag_afsluiten(self) -> None:
        w = self._pv_werkelijk
        if self._pv_voorspeld is not None and self._pv_voorspeld >= 1.0 and w.duur >= 20 * 3600:
            werkelijk = w.integraal / 3600.0 / 1000.0
            self.pv_verhoudingen = _voeg_toe(self.pv_verhoudingen, werkelijk / self._pv_voorspeld)

    def status(self) -> dict[str, Any]:
        return {
            "verbruik_marge": self.verbruik_marge,
            "verbruik_dagen_geleerd": len(self.verbruik_verhoudingen),
            "verbruik_verhoudingen": self.verbruik_verhoudingen,
            "pv_zeker_factor": self.pv_zeker_factor,
            "pv_dagen_geleerd": len(self.pv_verhoudingen),
            "pv_verhoudingen": self.pv_verhoudingen,
        }
