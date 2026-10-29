"""Sensoren van Batterij Planner Deye."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BatterijPlannerConfigEntry
from .entity import BatterijPlannerEntity
from .optimizer import MODI

DAGEN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MODUS_NAAM = {"zelfverbruik": "Self-consumption", "vasthouden": "Hold", "laden": "Charge", "ontladen": "Discharge"}

ICONEN = {
    "zelfverbruik": "mdi:home-battery",
    "vasthouden": "mdi:battery-lock",
    "laden": "mdi:battery-charging",
    "ontladen": "mdi:battery-arrow-down",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: BatterijPlannerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data
    entiteiten: list[SensorEntity] = [
        ModusSensor(c), VermogenSensor(c), StrategieSensor(c), BesparingSensor(c), VolgendeActieSensor(c),
        DoelSocSensor(c), VerbruikSensor(c),
        KostenSensor(c, "kosten_vandaag"), KostenSensor(c, "kosten_maand"), KostenSensor(c, "kosten_totaal"),
    ]
    if c.ev_ingeschakeld:
        entiteiten.append(AutoladerSensor(c))
    if c.wp_ingeschakeld:
        entiteiten.append(WarmtepompDoelSensor(c))
    async_add_entities(entiteiten)


class ModusSensor(BatterijPlannerEntity, SensorEntity):
    """Huidige modus + het volledige plan als attributen."""

    _attr_translation_key = "modus"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MODI)
    _unrecorded_attributes = frozenset({"blokken", "slots"})

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "modus")

    @property
    def native_value(self) -> str | None:
        return (self.coordinator.data or {}).get("modus")

    @property
    def icon(self) -> str:
        return ICONEN.get(self.native_value or "", "mdi:battery-alert")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data or {}
        sleutels = (
            "fout", "vermogen_pct", "strategie", "blok_einde", "soc_nu", "soc_doel_einde_blok", "kosten_plan",
            "kosten_zelfverbruik", "doorvoer_kwh", "horizon_einde", "kwartieren", "restwaarde_eur_kwh", "pv_bron",
            "pv_verwacht_kwh", "verbruik_verwacht_kwh", "auto_gepland_kwh", "afnameprijs_nu", "terugprijs_nu",
            "huisverbruik_w", "net_w", "deye_doel", "sturing_actief", "bijgewerkt", "blokken", "slots",
        )
        attrs = {k: d[k] for k in sleutels if k in d}
        attrs["laatste_schrijfacties"] = self.coordinator.laatste_schrijfacties
        return attrs


class VermogenSensor(BatterijPlannerEntity, SensorEntity):
    """Vermogenstrap van het huidige blok: 0 (zelfverbruik/vasthouden), 33, 66 of 100 %."""

    _attr_translation_key = "vermogen_pct"
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "vermogen_pct")

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("vermogen_pct")


class StrategieSensor(BatterijPlannerEntity, SensorEntity):
    """Terugverdienen (saldering t/m 2026) of laagste kosten (vanaf 2027)."""

    _attr_translation_key = "strategie"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["terugverdienen", "laagste_kosten"]
    _attr_icon = "mdi:strategy"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "strategie")

    @property
    def native_value(self) -> str | None:
        return (self.coordinator.data or {}).get("strategie")


class BesparingSensor(BatterijPlannerEntity, SensorEntity):
    _attr_translation_key = "besparing"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash-plus"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "besparing")

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("besparing")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data or {}
        return {"toelichting": "verschil met alleen zelfverbruik over de hele bekende prijshorizon",
                "horizon_einde": d.get("horizon_einde")}


class VolgendeActieSensor(BatterijPlannerEntity, SensorEntity):
    _attr_translation_key = "volgende_actie"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "volgende_actie")

    @property
    def native_value(self) -> str | None:
        blokken = (self.coordinator.data or {}).get("blokken") or []
        for b in blokken[1:]:
            if b["modus"] != "zelfverbruik":
                naam = MODUS_NAAM[b["modus"]]
                if b.get("vermogen_pct"):
                    naam += f" {b['vermogen_pct']}%"
                start = dt_util.parse_datetime(b["start"])
                eind = dt_util.parse_datetime(b["eind"])
                if start is None or eind is None:
                    return naam
                start, eind = dt_util.as_local(start), dt_util.as_local(eind)
                return f"{naam} {DAGEN[start.weekday()]} {start:%H:%M}–{eind:%H:%M}"
        return "None planned"


class DoelSocSensor(BatterijPlannerEntity, SensorEntity):
    _attr_translation_key = "doel_soc"
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:battery-arrow-up-outline"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "doel_soc")

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("soc_doel_einde_blok")


class VerbruikSensor(BatterijPlannerEntity, SensorEntity):
    _attr_translation_key = "verbruiksprofiel"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "verbruiksprofiel")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.profiel.laatste_kwartier_w

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p = self.coordinator.profiel
        return {"profiel_werkdag": p.werkdag, "profiel_weekend": p.weekend,
                "metingen_werkdag": p.n_werkdag, "metingen_weekend": p.n_weekend}


class KostenSensor(BatterijPlannerEntity, SensorEntity):
    """Werkelijke stroomkosten uit de P1-meter × Frank-kwartierprijs (negatief = opbrengst)."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash-multiple"

    def __init__(self, coordinator, sleutel: str) -> None:
        super().__init__(coordinator, sleutel)
        self._sleutel = sleutel
        self._attr_translation_key = sleutel
        if sleutel == "kosten_totaal":
            self._attr_state_class = SensorStateClass.TOTAL

    @property
    def available(self) -> bool:
        return super().available and self._sleutel in self.coordinator.kosten

    @property
    def native_value(self) -> float | None:
        waarde = self.coordinator.kosten.get(self._sleutel)
        return None if waarde is None else round(waarde, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._sleutel != "kosten_vandaag":
            return None
        k = self.coordinator.kosten
        return {"afname_kwh": k.get("afname_vandaag"), "teruglevering_kwh": k.get("terug_vandaag")}


class AutoladerSensor(BatterijPlannerEntity, SensorEntity):
    """Wat de planner met de auto doet en waarom."""

    _attr_translation_key = "auto"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["laden", "wachten", "niet_verbonden"]
    _attr_icon = "mdi:car-electric"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "auto")

    @property
    def native_value(self) -> str | None:
        return ((self.coordinator.data or {}).get("auto") or {}).get("stand")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        auto = dict((self.coordinator.data or {}).get("auto") or {})
        auto.pop("stand", None)
        auto["sturing_actief"] = self.coordinator.ev_actief
        auto["laatste_actie"] = self.coordinator.ev_laatste_actie
        return auto


class WarmtepompDoelSensor(BatterijPlannerEntity, SensorEntity):
    """Gewenste streeftemperatuur van het buffervat + waarom."""

    _attr_translation_key = "wp_doel"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = "°C"
    _attr_icon = "mdi:heat-pump-outline"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "wp_doel")

    @property
    def available(self) -> bool:
        return super().available and bool((self.coordinator.data or {}).get("warmtepomp"))

    @property
    def native_value(self) -> float | None:
        return ((self.coordinator.data or {}).get("warmtepomp") or {}).get("doel")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        wp = dict((self.coordinator.data or {}).get("warmtepomp") or {})
        wp.pop("doel", None)
        return wp
