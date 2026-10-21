"""Binary sensors: show running, show hours, background music, connected."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PlayerConfigEntry, PlayerCoordinator
from .entity import PlayerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: PlayerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities([ShowRunning(c), ShowHours(c), BackgroundMusic(c), Connected(c)])


class ShowRunning(PlayerEntity, BinarySensorEntity):
    """On while a show plays (including paused, and its Start Main Show fade)."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: PlayerCoordinator) -> None:
        super().__init__(coordinator, "show_running")

    @property
    def is_on(self) -> bool:
        return self.show.get("state") not in (None, "idle")


class ShowHours(PlayerEntity, BinarySensorEntity):
    """On while a scheduled show window is open."""

    def __init__(self, coordinator: PlayerCoordinator) -> None:
        super().__init__(coordinator, "show_hours")

    @property
    def is_on(self) -> bool:
        return bool(self.schedule.get("active"))

    @property
    def extra_state_attributes(self) -> dict:
        a = self.schedule.get("active") or {}
        return {"playlist": a.get("playlist"), "ends": a.get("end")}


class BackgroundMusic(PlayerEntity, BinarySensorEntity):
    """On while the pre-show background music plays."""

    def __init__(self, coordinator: PlayerCoordinator) -> None:
        super().__init__(coordinator, "background_music")

    @property
    def is_on(self) -> bool:
        music = (self.coordinator.data or {}).get("music") or {}
        return music.get("state") == "playing"

    @property
    def extra_state_attributes(self) -> dict:
        music = (self.coordinator.data or {}).get("music") or {}
        return {"title": music.get("title")}


class Connected(PlayerEntity, BinarySensorEntity):
    """Whether Home Assistant has a live connection to the Player.

    Always available, so it can drive an alert while the Player is down.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PlayerCoordinator) -> None:
        super().__init__(coordinator, "connected")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.connected

    @property
    def extra_state_attributes(self) -> dict:
        seen = self.coordinator.last_seen
        return {"last_seen": seen.isoformat() if seen else None, "address": self.coordinator.client.host}
