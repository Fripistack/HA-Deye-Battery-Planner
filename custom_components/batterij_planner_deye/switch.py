"""Schakelaars om de sturing van omvormers, autolader en warmtepomp aan/uit te zetten."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import BatterijPlannerConfigEntry
from .entity import BatterijPlannerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: BatterijPlannerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data
    entiteiten: list[SwitchEntity] = [SturingSwitch(c)]
    if c.ev_ingeschakeld:
        entiteiten.append(AutoladerSwitch(c))
    if c.wp_ingeschakeld:
        entiteiten.append(WarmtepompSwitch(c))
    async_add_entities(entiteiten)


class _HerstelSwitch(BatterijPlannerEntity, SwitchEntity, RestoreEntity):
    """Schakelaar die zijn stand na een herstart terugzet."""

    _vlag = ""

    def __init__(self, coordinator, sleutel: str) -> None:
        super().__init__(coordinator, sleutel)
        self._attr_translation_key = sleutel

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        vorig = await self.async_get_last_state()
        if vorig is not None and vorig.state == "on":
            setattr(self.coordinator, self._vlag, True)
            self.hass.async_create_task(self.coordinator.async_request_refresh())

    @property
    def is_on(self) -> bool:
        return bool(getattr(self.coordinator, self._vlag))

    async def async_turn_on(self, **kwargs: Any) -> None:
        setattr(self.coordinator, self._vlag, True)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        setattr(self.coordinator, self._vlag, False)
        self.async_write_ha_state()
        await self._terug_naar_normaal()
        await self.coordinator.async_request_refresh()

    async def _terug_naar_normaal(self) -> None:
        raise NotImplementedError


class SturingSwitch(_HerstelSwitch):
    """Uit = alleen rekenen (meekijken), aan = Deye-omvormers aansturen."""

    _attr_icon = "mdi:robot"
    _vlag = "actief"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "sturing")

    async def _terug_naar_normaal(self) -> None:
        await self.coordinator.async_naar_zelfverbruik()


class AutoladerSwitch(_HerstelSwitch):
    """Uit = alleen adviseren, aan = evcc-laadmodus aansturen."""

    _attr_icon = "mdi:ev-station"
    _vlag = "ev_actief"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "ev_sturing")

    async def _terug_naar_normaal(self) -> None:
        await self.coordinator.async_ev_normaal()


class WarmtepompSwitch(_HerstelSwitch):
    """Uit = alleen adviseren, aan = streeftemperatuur buffervat (of SG Ready) aansturen."""

    _attr_icon = "mdi:heat-pump"
    _vlag = "wp_actief"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "wp_sturing")

    async def _terug_naar_normaal(self) -> None:
        await self.coordinator.async_wp_normaal()
