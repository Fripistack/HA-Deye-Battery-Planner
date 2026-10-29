"""Zelflerend verbruiksprofiel per uur (werkdag / weekend), opgeslagen in .storage."""
from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

ALPHA_WERKDAG = 0.1
ALPHA_WEEKEND = 0.2


class VerbruiksProfiel:
    """Tijdgewogen kwartiergemiddelde -> exponentieel voortschrijdend gemiddelde per uur."""

    def __init__(self, hass: HomeAssistant, entry_id: str, start_w: float) -> None:
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry_id}.profiel")
        self.werkdag: list[float] = [start_w] * 24
        self.weekend: list[float] = [start_w] * 24
        self.n_werkdag: list[int] = [0] * 24
        self.n_weekend: list[int] = [0] * 24
        self.laatste_kwartier_w: float | None = None
        self._integraal = 0.0
        self._duur = 0.0
        self._w: float | None = None
        self._t: datetime | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        for naam in ("werkdag", "weekend", "n_werkdag", "n_weekend"):
            waarde = data.get(naam)
            if isinstance(waarde, list) and len(waarde) == 24:
                setattr(self, naam, waarde)

    def _data(self) -> dict:
        return {
            "werkdag": self.werkdag,
            "weekend": self.weekend,
            "n_werkdag": self.n_werkdag,
            "n_weekend": self.n_weekend,
        }

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    def meting(self, watt: float | None, t: datetime) -> None:
        """Nieuwe waarde van de verbruikssensor (of None bij onbekend)."""
        if self._w is not None and self._t is not None:
            dt = (t - self._t).total_seconds()
            if 0 < dt < 3600:
                self._integraal += self._w * dt
                self._duur += dt
        self._w = watt
        self._t = t

    def afsluiten(self, t: datetime) -> None:
        """Aan het eind van elk kwartier: gemiddelde verwerken in het profiel."""
        self.meting(self._w, t)
        if self._duur >= 300:
            gem = max(0.0, self._integraal / self._duur)
            self.laatste_kwartier_w = round(gem, 0)
            moment = t - timedelta(minutes=1)
            uur = moment.hour
            if moment.weekday() >= 5:
                prof, tel, basis = self.weekend, self.n_weekend, ALPHA_WEEKEND
            else:
                prof, tel, basis = self.werkdag, self.n_werkdag, ALPHA_WERKDAG
            # eerste metingen tellen zwaar zodat het profiel snel bruikbaar is
            alpha = max(basis, 1.0 / (tel[uur] + 1))
            prof[uur] = round(prof[uur] * (1 - alpha) + gem * alpha, 1)
            tel[uur] = min(tel[uur] + 1, 1000)
            self._store.async_delay_save(self._data, 120)
        self._integraal = 0.0
        self._duur = 0.0
