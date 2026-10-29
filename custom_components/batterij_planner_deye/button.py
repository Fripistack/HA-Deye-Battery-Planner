"""Knop om het plan direct opnieuw te berekenen."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BatterijPlannerConfigEntry
from .entity import BatterijPlannerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: BatterijPlannerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([HerberekenKnop(entry.runtime_data)])


class HerberekenKnop(BatterijPlannerEntity, ButtonEntity):
    _attr_translation_key = "herbereken"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "herbereken")

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
