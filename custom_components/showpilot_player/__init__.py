"""The ShowPilot Player integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PlayerClient
from .const import CONF_PASSWORD
from .coordinator import PlayerConfigEntry, PlayerCoordinator

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: PlayerConfigEntry) -> bool:
    client = PlayerClient(
        entry.data[CONF_HOST], async_get_clientsession(hass), entry.data.get(CONF_PASSWORD)
    )
    coordinator = PlayerCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PlayerConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_close()
    return ok


async def _async_reload_entry(hass: HomeAssistant, entry: PlayerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
