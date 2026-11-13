"""Keuze van de gebruiksmodus van de planner."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BatterijPlannerConfigEntry
from .entity import BatterijPlannerEntity
from .optimizer import GEBRUIKSMODI


async def async_setup_entry(
    hass: HomeAssistant, entry: BatterijPlannerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([GebruiksmodusSelect(entry.runtime_data)])


class GebruiksmodusSelect(BatterijPlannerEntity, SelectEntity):
    """Optimaal (laagste kosten / terugverdienen) of goedkoop inkopen voor eigen verbruik.

    De keuze wordt door de coordinator zelf bewaard en al vóór de eerste berekening
    geladen, zodat de omvormer na een herstart nooit even in de verkeerde modus staat.
    """

    _attr_translation_key = "gebruiksmodus"
    _attr_options = list(GEBRUIKSMODI)
    _attr_icon = "mdi:tune-variant"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "gebruiksmodus")

    @property
    def current_option(self) -> str:
        return self.coordinator.gebruiksmodus

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_zet_gebruiksmodus(option)
        self.async_write_ha_state()
