"""Base entity for the ShowPilot Player integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import PlayerCoordinator


class PlayerEntity(CoordinatorEntity[PlayerCoordinator]):
    """An entity on the Player's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PlayerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_uid}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = coordinator.device_info()

    @property
    def show(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("show") or {}

    @property
    def schedule(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("schedule") or {}
