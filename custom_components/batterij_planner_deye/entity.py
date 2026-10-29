"""Gedeelde basis voor de entiteiten."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BatterijPlannerCoordinator


class BatterijPlannerEntity(CoordinatorEntity[BatterijPlannerCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: BatterijPlannerCoordinator, sleutel: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{sleutel}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Batterij Planner Deye",
            manufacturer="Fripistack",
            model="Deye + evcc kwartierprijs-optimalisatie",
            entry_type=DeviceEntryType.SERVICE,
        )
