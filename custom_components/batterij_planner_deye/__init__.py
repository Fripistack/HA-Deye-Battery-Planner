"""Batterij Planner Deye: optimale sturing van Deye-hybride omvormers, autolader en warmtepomp op dynamische kwartierprijzen."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import BatterijPlannerCoordinator

type BatterijPlannerConfigEntry = ConfigEntry[BatterijPlannerCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: BatterijPlannerConfigEntry) -> bool:
    coordinator = BatterijPlannerCoordinator(hass, entry)
    await coordinator.async_start()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_opties_gewijzigd))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BatterijPlannerConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok


async def _opties_gewijzigd(hass: HomeAssistant, entry: BatterijPlannerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
