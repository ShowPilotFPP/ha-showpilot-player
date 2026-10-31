"""Config flow: found on the network, or by address; then paired.

Pairing (Player 0.13.1+): Home Assistant asks the Player for an access key,
the Player shows "Allow Home Assistant?" with a 4-digit code on its admin
pages, and once someone presses Allow the key is handed over. The same
pairing runs when the Player is added, when it asks to sign in again, and
on Reconfigure, so an existing setup is re-paired in place (entities,
automations and history stay).

Older Players: a user name and password (0.13.0), or only a password.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession

if TYPE_CHECKING:  # moved between Home Assistant versions; only its attributes are used
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    PairingDenied,
    PairingNotSupported,
    PlayerAuthError,
    PlayerClient,
    PlayerConnectionError,
    PlayerError,
)
from .const import CONF_ACCESS_KEY, CONF_PASSWORD, CONF_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

MIN_PLAYER = (0, 11, 0)  # first version with the live event feed

USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})
PASSWORD_SCHEMA = vol.Schema({vol.Optional(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})


def _version(v: str) -> tuple[int, ...]:
    parts = []
    for p in str(v).split("-")[0].split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


class ShowPilotPlayerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a ShowPilot Player, or pair it again."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._name: str | None = None
        self._client: PlayerClient | None = None
        self._pair: dict[str, Any] | None = None
        self._pair_task: asyncio.Task | None = None

    # ------------------------------------------------------------ helpers
    def _new_client(self, **auth: Any) -> PlayerClient:
        return PlayerClient(self._host, async_get_clientsession(self.hass), **auth)

    async def _check(self, client: PlayerClient) -> tuple[dict[str, Any] | None, str | None]:
        """Read /status with the client's sign-in: (status, None) or (None, error)."""
        try:
            status = await client.status()
        except PlayerAuthError:
            return None, "invalid_auth"
        except PlayerConnectionError:
            return None, "cannot_connect"
        except PlayerError:
            return None, "not_player"
        except Exception:
            _LOGGER.exception("Unexpected error talking to ShowPilot Player at %s", self._host)
            return None, "unknown"
        if _version(status.get("version", "0")) < MIN_PLAYER:
            return None, "too_old"
        if not status.get("uuid"):
            return None, "not_player"
        return status, None

    async def _connect(self) -> tuple[ConfigFlowResult | None, str | None]:
        """Start pairing with self._host (or fall back for older Players).
        Returns (result, None) to go on, or (None, error) for the form."""
        client = self._new_client()
        try:
            info = await client.auth_info()
        except PlayerConnectionError:
            return None, "cannot_connect"
        except PlayerError:
            return None, "not_player"
        try:
            self._pair = await client.pair_request(f"Home Assistant ({self.hass.config.location_name})")
        except PairingNotSupported:
            if info.get("required"):
                return await self.async_step_password(), None
            status, error = await self._check(client)
            return (None, error) if error else (await self._done(status, {CONF_HOST: self._host}), None)
        except PlayerConnectionError:
            return None, "cannot_connect"
        except PlayerError as err:
            _LOGGER.warning("Pairing with %s refused: %s", self._host, err)
            return None, "pair_refused"
        self._client = client
        self._pair_task = None
        return await self.async_step_pair(), None

    async def _done(self, status: dict[str, Any], data: dict[str, Any]) -> ConfigFlowResult:
        """Save: a new Player, or new sign-in details for an existing one."""
        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            entry = self._get_reauth_entry() if self.source == SOURCE_REAUTH else self._get_reconfigure_entry()
            if entry.unique_id and str(status["uuid"]) != entry.unique_id:
                return self.async_abort(reason="wrong_device")
            return self.async_update_reload_and_abort(entry, data=data)
        await self.async_set_unique_id(str(status["uuid"]))
        self._abort_if_unique_id_configured(updates=data)
        return self.async_create_entry(
            title=str(status.get("hostName") or f"ShowPilot Player {self._host}"), data=data
        )

    # ------------------------------------------------------------ pairing
    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Wait for someone to press Allow on the Player."""
        assert self._client and self._pair
        if self._pair_task is None:
            self._pair_task = self.hass.async_create_task(
                self._client.wait_for_pairing(self._pair["id"], self._pair["poll"])
            )
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="pair",
                progress_task=self._pair_task,
                description_placeholders={"code": str(self._pair.get("code", "")), "host": self._host or ""},
            )
        try:
            self._pair_task.result()
        except PairingDenied:
            return self.async_show_progress_done(next_step_id="pair_denied")
        except Exception:
            _LOGGER.exception("Pairing with %s failed", self._host)
            return self.async_show_progress_done(next_step_id="pair_denied")
        finally:
            self._pair_task = None
        return self.async_show_progress_done(next_step_id="pair_finish")

    async def async_step_pair_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._client
        status, error = await self._check(self._client)
        if error:
            return self.async_abort(reason=error if error in ("too_old", "not_player", "cannot_connect") else "unknown")
        return await self._done(status, {CONF_HOST: self._host, CONF_ACCESS_KEY: self._client._access_key})

    async def async_step_pair_denied(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Denied, or nobody answered within 10 minutes: offer to ask again."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result, error = await self._connect()
            if result:
                return result
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="pair_denied", errors=errors, description_placeholders={"host": self._host or ""}
        )

    # ------------------------------------------------------------ older Players
    async def async_step_password(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Player 0.13.0 (user name + password) or older (password only)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = (user_input.get(CONF_USERNAME) or "").strip() or None
            client = self._new_client(password=user_input[CONF_PASSWORD], username=username)
            try:
                await client.login()
                status, error = await self._check(client)
            except PlayerAuthError:
                status, error = None, "invalid_auth"
            except PlayerConnectionError:
                status, error = None, "cannot_connect"
            except PlayerError:
                status, error = None, "not_player"
            if error:
                errors["base"] = error
            else:
                data = {CONF_HOST: self._host, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                if username:
                    data[CONF_USERNAME] = username
                return await self._done(status, data)
        return self.async_show_form(
            step_id="password",
            data_schema=PASSWORD_SCHEMA,
            errors=errors,
            description_placeholders={"host": self._host or ""},
        )

    # ------------------------------------------------------------ by address
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            result, error = await self._connect()
            if result:
                return result
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    # ------------------------------------------------------------ discovered
    async def async_step_zeroconf(self, discovery_info: "ZeroconfServiceInfo") -> ConfigFlowResult:
        uuid = (discovery_info.properties or {}).get("uuid")
        if not uuid:
            return self.async_abort(reason="not_player")
        port = discovery_info.port or 80
        host = str(discovery_info.host) if port == 80 else f"{discovery_info.host}:{port}"
        await self.async_set_unique_id(uuid)
        # A known Player at a new address: just update the address.
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._host = host
        self._name = discovery_info.name.split("._showpilot-player")[0].replace("ShowPilot Player on ", "")
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            result, error = await self._connect()
            if result:
                return result
            errors["base"] = error
        return self.async_show_form(
            step_id="zeroconf_confirm",
            errors=errors,
            description_placeholders={"name": self._name or "", "host": self._host or ""},
        )

    # ------------------------------------------------------------ an existing Player
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The Player refused Home Assistant: pair it again."""
        entry = self._get_reauth_entry()
        self._host = entry.data[CONF_HOST]
        errors: dict[str, str] = {}
        if user_input is not None:
            result, error = await self._connect()
            if result:
                return result
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Check the address and pair again; entities and automations stay."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            result, error = await self._connect()
            if result:
                return result
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input or entry.data),
            errors=errors,
            description_placeholders={"name": entry.title},
        )
