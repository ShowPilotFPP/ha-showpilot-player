"""Buttons: stop after this song, stop now, next song, Start Main Show, restart."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PlayerClient
from .coordinator import PlayerConfigEntry, PlayerCoordinator
from .entity import PlayerEntity


@dataclass(frozen=True, kw_only=True)
class PlayerButtonDescription(ButtonEntityDescription):
    press: Callable[[PlayerClient], Awaitable[Any]]


BUTTONS = [
    PlayerButtonDescription(key="stop_after_song", press=lambda c: c.stop(graceful=True)),
    PlayerButtonDescription(key="stop_now", press=lambda c: c.stop(graceful=False)),
    PlayerButtonDescription(key="next_song", press=lambda c: c.next()),
    PlayerButtonDescription(key="start_main_show", press=lambda c: c.start_main_show()),
    PlayerButtonDescription(
        key="restart",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press=lambda c: c.restart(),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: PlayerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(PlayerButton(entry.runtime_data, d) for d in BUTTONS)


class PlayerButton(PlayerEntity, ButtonEntity):
    entity_description: PlayerButtonDescription

    def __init__(self, coordinator: PlayerCoordinator, description: PlayerButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.coordinator.async_command(self.entity_description.press, self.coordinator.client)
