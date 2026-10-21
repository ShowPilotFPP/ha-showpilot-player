"""Live connection to one ShowPilot Player.

Nothing is polled: the coordinator keeps the Player's event feed open and
every message updates the entities at once. Show events are also fired on
the event bus (showpilot_player_event) for automations and device triggers.
When the connection drops, entities go unavailable (except "Connected") and
it reconnects with a short back-off.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
import json
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PlayerAuthError, PlayerClient, PlayerConnectionError, PlayerError
from .const import DOMAIN, EVENT_SHOW, RECEIVE_TIMEOUT, RECONNECT_DELAYS

_LOGGER = logging.getLogger(__name__)

type PlayerConfigEntry = ConfigEntry[PlayerCoordinator]


class PlayerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Hold the Player's latest state, pushed over its event feed."""

    config_entry: PlayerConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: PlayerConfigEntry, client: PlayerClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host}",
            update_interval=None,  # push only
        )
        self.client = client
        self.device_uid = entry.unique_id or entry.entry_id
        self.connected = False
        self.last_seen: datetime | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._closing = False
        self._resume_seen: str | None = None
        self._connect_logged = False

    # ------------------------------------------------------------ first state
    async def _async_update_data(self) -> dict[str, Any]:
        """Open the feed and take the first state message (setup only)."""
        try:
            ws = await self.client.connect_events()
            state = await self._first_state(ws)
        except PlayerAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (PlayerError, asyncio.TimeoutError, aiohttp.ClientError) as err:
            raise UpdateFailed(str(err)) from err
        self._ws = ws
        self._note_resume(state, fire=False)
        self._mark_connected()
        return state

    async def _first_state(self, ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
        msg = await ws.receive(timeout=15)
        if msg.type != aiohttp.WSMsgType.TEXT:
            await ws.close()
            raise PlayerConnectionError("the Player closed the event feed")
        data = json.loads(msg.data)
        if data.get("type") != "state" or not isinstance(data.get("state"), dict):
            await ws.close()
            raise PlayerError("unexpected first message on the event feed")
        return data["state"]

    def start(self) -> None:
        """Keep the feed open from now on (call after the first refresh)."""
        self._task = self.config_entry.async_create_background_task(
            self.hass, self._run(), f"{DOMAIN} event feed {self.client.host}"
        )

    async def async_close(self) -> None:
        self._closing = True
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()

    # ------------------------------------------------------------ the feed
    async def _run(self) -> None:
        attempt = 0
        while not self._closing:
            ws, self._ws = self._ws, None
            if ws is not None and ws.closed:
                ws = None
            try:
                if ws is None:
                    ws = await self.client.connect_events()
                    state = await self._first_state(ws)
                    self._mark_connected()  # before the update, so "Connected" turns on with it
                    self.async_set_updated_data(state)
                    self._note_resume(state, fire=True)
                attempt = 0
                self._ws = ws
                await self._consume(ws)
                if not self._closing:
                    _LOGGER.debug("%s closed the event feed", self.client.host)
            except PlayerAuthError as err:
                _LOGGER.warning("ShowPilot Player %s: %s", self.client.host, err)
                self._mark_disconnected(err)
                self.config_entry.async_start_reauth(self.hass)
                return
            except asyncio.CancelledError:
                raise
            except (PlayerError, asyncio.TimeoutError, aiohttp.ClientError, ValueError) as err:
                if not self._closing:
                    _LOGGER.debug("event feed from %s: %s", self.client.host, err)
            finally:
                self._ws = None
                if ws is not None and not ws.closed:
                    await ws.close()
            if self._closing:
                return
            self._mark_disconnected(UpdateFailed(f"lost the connection to {self.client.host}"))
            await asyncio.sleep(RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)])
            attempt += 1

    async def _consume(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            # The Player pings every 30 s; aiohttp answers and keeps waiting.
            msg = await ws.receive(timeout=RECEIVE_TIMEOUT)
            if msg.type == aiohttp.WSMsgType.TEXT:
                self._handle(json.loads(msg.data))
            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                return

    def _handle(self, msg: dict[str, Any]) -> None:
        self.last_seen = dt_util.utcnow()
        state = msg.get("state")
        if isinstance(state, dict):
            self.async_set_updated_data(state)
            self._note_resume(state, fire=True)
        if msg.get("type") == "event" and msg.get("event"):
            self._fire(str(msg["event"]), msg.get("data") or {})

    def _note_resume(self, state: dict[str, Any], fire: bool) -> None:
        """The Player resumes at start-up, before anyone is connected: it
        shows up as a new lastResume in the state after the reconnect."""
        last = state.get("lastResume") or {}
        at = last.get("at")
        if not at or at == self._resume_seen:
            return
        first = self._resume_seen is None
        self._resume_seen = at
        if not fire and first:
            # At setup, fire it only if it just happened (HA started with the Player).
            when = dt_util.parse_datetime(at)
            if not when or (dt_util.utcnow() - when).total_seconds() > 120:
                return
        self._fire("show_resumed", last)

    def _fire(self, event: str, data: dict[str, Any]) -> None:
        device = dr.async_get(self.hass).async_get_device(identifiers={(DOMAIN, self.device_uid)})
        self.hass.bus.async_fire(
            EVENT_SHOW,
            {
                "device_id": device.id if device else None,
                "type": event,
                **{k: v for k, v in data.items() if k not in ("device_id", "type")},
            },
        )

    def _mark_connected(self) -> None:
        self.connected = True
        self.last_seen = dt_util.utcnow()
        if self._connect_logged:
            _LOGGER.info("ShowPilot Player %s is back", self.client.host)
        self._connect_logged = True

    def _mark_disconnected(self, err: Exception) -> None:
        if self.connected:
            _LOGGER.info("lost the connection to ShowPilot Player %s; reconnecting", self.client.host)
        self.connected = False
        self.async_set_update_error(err)

    # ------------------------------------------------------------ commands
    async def async_command(self, func: Callable[..., Awaitable[Any]], *args: Any) -> Any:
        """Send a command; the result arrives over the feed by itself."""
        try:
            return await func(*args)
        except PlayerError as err:
            raise HomeAssistantError(f"ShowPilot Player: {err}") from err

    # ------------------------------------------------------------ device
    @property
    def player_name(self) -> str:
        host = (self.data or {}).get("host")
        return str(host) if host else f"ShowPilot Player {self.client.host}"

    def device_info(self) -> DeviceInfo:
        data = self.data or {}
        info = DeviceInfo(
            identifiers={(DOMAIN, self.device_uid)},
            name=self.player_name,
            manufacturer="ShowPilot",
            model="ShowPilot Player",
            configuration_url=self.client.base_url + "/",
        )
        if data.get("version"):
            info["sw_version"] = str(data["version"])
        return info
