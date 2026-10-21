"""The show as a media player: what's playing, play/pause/stop/next, and
playlists as sources. No volume, on purpose: the level is set once at the
amplifier or FM transmitter."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import PlayerConfigEntry, PlayerCoordinator
from .entity import PlayerEntity

SERVICE_START_PLAYLIST = "start_playlist"
SERVICE_INSERT_PLAYLIST = "insert_playlist"
SERVICE_RUN_COMMAND = "run_command"
SERVICE_RUN_PRESET = "run_preset"
SERVICE_START_MAIN_SHOW = "start_main_show"
SERVICE_STOP_AFTER_SONG = "stop_after_song"

FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.SELECT_SOURCE
)


async def async_setup_entry(
    hass: HomeAssistant, entry: PlayerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([ShowPlayer(entry.runtime_data)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_START_PLAYLIST,
        {vol.Required("playlist"): cv.string, vol.Optional("repeat", default=False): cv.boolean},
        "async_start_playlist",
    )
    platform.async_register_entity_service(
        SERVICE_INSERT_PLAYLIST,
        {vol.Required("playlist"): cv.string, vol.Optional("when", default="after_song"): vol.In(["after_song", "now"])},
        "async_insert_playlist",
    )
    platform.async_register_entity_service(
        SERVICE_RUN_COMMAND,
        {vol.Required("command"): cv.string, vol.Optional("args", default=[]): vol.All(cv.ensure_list, [cv.string])},
        "async_run_command",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_RUN_PRESET, {vol.Required("preset"): cv.string}, "async_run_preset"
    )
    platform.async_register_entity_service(SERVICE_START_MAIN_SHOW, {}, "async_start_main_show")
    platform.async_register_entity_service(SERVICE_STOP_AFTER_SONG, {}, "async_stop_after_song")


class ShowPlayer(PlayerEntity, MediaPlayerEntity):
    _attr_supported_features = FEATURES
    _attr_media_content_type = MediaType.MUSIC
    _attr_name = None  # the device's own name: "Front yard Player"

    def __init__(self, coordinator: PlayerCoordinator) -> None:
        super().__init__(coordinator, "show")
        self._last_playlist: str | None = None

    # ------------------------------------------------------------ state
    @property
    def state(self) -> MediaPlayerState:
        s = self.show.get("state")
        if s in ("playing", "stopping", "starting"):
            return MediaPlayerState.PLAYING
        if s == "paused":
            return MediaPlayerState.PAUSED
        return MediaPlayerState.IDLE

    @property
    def source(self) -> str | None:
        p = self.show.get("playlist")
        if p:
            self._last_playlist = p
        return p

    @property
    def source_list(self) -> list[str]:
        return list((self.coordinator.data or {}).get("playlists") or [])

    @property
    def media_title(self) -> str | None:
        return self.show.get("song")

    @property
    def media_playlist(self) -> str | None:
        return self.show.get("playlist")

    @property
    def media_duration(self) -> float | None:
        ms = self.show.get("durationMs")
        return ms / 1000 if ms else None

    # The Player sends when the song's position was 0; Home Assistant counts
    # on from there, so the position stays right without any updates.
    @property
    def media_position(self) -> float | None:
        return 0 if self._started_at() else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self._started_at()

    def _started_at(self) -> datetime | None:
        v = self.show.get("songStartedAt")
        return dt_util.parse_datetime(v) if v and self.state == MediaPlayerState.PLAYING else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "next_song": self.show.get("next"),
            "request": bool(self.show.get("request")),
            "show_state": self.show.get("state"),
            "sequence": self.show.get("sequence"),
            "media": self.show.get("media"),
        }

    # ------------------------------------------------------------ controls
    async def _cmd(self, func, *args):
        return await self.coordinator.async_command(func, *args)

    async def async_media_play(self) -> None:
        if self.show.get("state") == "paused":
            await self._cmd(self.coordinator.client.toggle_pause)
            return
        if self.show.get("state") not in (None, "idle"):
            return
        name = self._last_playlist or ((self.schedule.get("active") or {}).get("playlist"))
        if not name:
            raise HomeAssistantError("Pick a playlist (source) to start the show")
        await self._cmd(self.coordinator.client.play_playlist, name, False)

    async def async_media_pause(self) -> None:
        if self.show.get("state") in ("playing", "stopping"):
            await self._cmd(self.coordinator.client.toggle_pause)

    async def async_media_stop(self) -> None:
        await self._cmd(self.coordinator.client.stop, False)

    async def async_media_next_track(self) -> None:
        await self._cmd(self.coordinator.client.next)

    async def async_media_previous_track(self) -> None:
        await self._cmd(self.coordinator.client.previous)

    async def async_select_source(self, source: str) -> None:
        await self._cmd(self.coordinator.client.play_playlist, source, False)

    # ------------------------------------------------------------ services
    async def async_start_playlist(self, playlist: str, repeat: bool = False) -> None:
        await self._cmd(self.coordinator.client.play_playlist, playlist, repeat)

    async def async_insert_playlist(self, playlist: str, when: str = "after_song") -> None:
        await self._cmd(self.coordinator.client.insert_playlist, playlist, when == "now")

    async def async_run_command(self, command: str, args: list[str] | None = None) -> ServiceResponse:
        result = await self._cmd(self.coordinator.client.run_command, command, list(args or []))
        return {"result": (result or {}).get("result") if isinstance(result, dict) else result}

    async def async_run_preset(self, preset: str) -> None:
        await self._cmd(self.coordinator.client.run_preset, preset)

    async def async_start_main_show(self) -> None:
        await self._cmd(self.coordinator.client.start_main_show)

    async def async_stop_after_song(self) -> None:
        await self._cmd(self.coordinator.client.stop, True)
