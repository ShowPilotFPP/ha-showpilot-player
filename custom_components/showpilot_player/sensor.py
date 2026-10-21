"""Sensors: status, playlist, song, next song, next show, show ends."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import SHOW_STATES
from .coordinator import PlayerConfigEntry, PlayerCoordinator
from .entity import PlayerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: PlayerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities(
        [
            Status(c),
            ShowText(c, "playlist", ("show", "playlist")),
            ShowText(c, "song", ("show", "song")),
            ShowText(c, "next_song", ("show", "next")),
            ShowTime(c, "next_show", ("schedule", "next", "start")),
            ShowTime(c, "show_ends", ("schedule", "active", "end")),
            LastResumed(c),
        ]
    )


def _dig(data: dict | None, path: tuple[str, ...]):
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


class Status(PlayerEntity, SensorEntity):
    """idle / starting / playing / paused / stopping."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SHOW_STATES

    def __init__(self, coordinator: PlayerCoordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str | None:
        state = self.show.get("state")
        return state if state in SHOW_STATES else None


class ShowText(PlayerEntity, SensorEntity):
    def __init__(self, coordinator: PlayerCoordinator, key: str, path: tuple[str, ...]) -> None:
        super().__init__(coordinator, key)
        self._path = path

    @property
    def native_value(self) -> str | None:
        v = _dig(self.coordinator.data, self._path)
        return str(v) if v else None


class ShowTime(PlayerEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: PlayerCoordinator, key: str, path: tuple[str, ...]) -> None:
        super().__init__(coordinator, key)
        self._path = path

    @property
    def native_value(self) -> datetime | None:
        v = _dig(self.coordinator.data, self._path)
        return dt_util.parse_datetime(v) if isinstance(v, str) else None

    @property
    def extra_state_attributes(self) -> dict:
        parent = _dig(self.coordinator.data, self._path[:-1]) or {}
        return {"playlist": parent.get("playlist") or parent.get("name")}


class LastResumed(PlayerEntity, SensorEntity):
    """When the Player last carried on a show after restarting."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PlayerCoordinator) -> None:
        super().__init__(coordinator, "last_resumed")

    @property
    def native_value(self) -> datetime | None:
        last = (self.coordinator.data or {}).get("lastResume") or {}
        return dt_util.parse_datetime(last["at"]) if last.get("at") else None

    @property
    def extra_state_attributes(self) -> dict:
        last = (self.coordinator.data or {}).get("lastResume") or {}
        return {k: last.get(k) for k in ("playlist", "song", "item", "source", "downSec")}
