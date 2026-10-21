"""Config flow: found on the network, or by address; admin password if set."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession

if TYPE_CHECKING:  # moved between Home Assistant versions; only its attributes are used
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import PlayerAuthError, PlayerClient, PlayerConnectionError, PlayerError
from .const import CONF_PASSWORD, DOMAIN

_LOGGER = logging.getLogger(__name__)

MIN_PLAYER = (0, 11, 0)  # first version with the live event feed

USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})
PASSWORD_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


def _version(v: str) -> tuple[int, ...]:
    parts = []
    for p in str(v).split("-")[0].split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


class ShowPilotPlayerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a ShowPilot Player."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._name: str | None = None

    async def _probe(
        self, host: str, password: str | None
    ) -> tuple[dict[str, Any] | None, str | None]:
        """(status, None) when it works; (None, error) otherwise. The error
        "password_needed" means: ask for the admin password."""
        client = PlayerClient(host, async_get_clientsession(self.hass), password)
        try:
            if await client.auth_required():
                if not password:
                    return None, "password_needed"
                await client.login()
            status = await client.status()
        except PlayerAuthError:
            return None, "invalid_auth"
        except PlayerConnectionError:
            return None, "cannot_connect"
        except PlayerError:
            return None, "not_player"
        except Exception:
            _LOGGER.exception("Unexpected error talking to ShowPilot Player at %s", host)
            return None, "unknown"
        if _version(status.get("version", "0")) < MIN_PLAYER:
            return None, "too_old"
        if not status.get("uuid"):
            return None, "not_player"
        return status, None

    async def _finish(self, status: dict[str, Any], host: str, password: str | None) -> ConfigFlowResult:
        await self.async_set_unique_id(str(status["uuid"]))
        data = {CONF_HOST: host}
        if password:
            data[CONF_PASSWORD] = password
        self._abort_if_unique_id_configured(updates=data)
        return self.async_create_entry(
            title=str(status.get("hostName") or f"ShowPilot Player {host}"), data=data
        )

    # ------------------------------------------------------------ by address
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            status, error = await self._probe(host, None)
            if error == "password_needed":
                self._host = host
                return await self.async_step_password()
            if error:
                errors["base"] = error
            else:
                return await self._finish(status, host, None)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_password(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            status, error = await self._probe(self._host, user_input[CONF_PASSWORD])
            if error and error != "password_needed":
                errors["base"] = error
            elif status:
                return await self._finish(status, self._host, user_input[CONF_PASSWORD])
        return self.async_show_form(
            step_id="password",
            data_schema=PASSWORD_SCHEMA,
            errors=errors,
            description_placeholders={"host": self._host or ""},
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
            status, error = await self._probe(self._host, None)
            if error == "password_needed":
                return await self.async_step_password()
            if error:
                errors["base"] = error
            else:
                return await self._finish(status, self._host, None)
        return self.async_show_form(
            step_id="zeroconf_confirm",
            errors=errors,
            description_placeholders={"name": self._name or "", "host": self._host or ""},
        )

    # ------------------------------------------------------------ changes
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input.get(CONF_PASSWORD) or None
            status, error = await self._probe(entry.data[CONF_HOST], password)
            if error:
                errors["base"] = "invalid_auth" if error == "password_needed" else error
            else:
                data = {CONF_HOST: entry.data[CONF_HOST]}
                if password:
                    data[CONF_PASSWORD] = password
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the Player's address."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            status, error = await self._probe(host, entry.data.get(CONF_PASSWORD))
            if error == "password_needed":
                error = "invalid_auth"
            if error:
                errors["base"] = error
            elif entry.unique_id and str(status["uuid"]) != entry.unique_id:
                return self.async_abort(reason="wrong_device")
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input or entry.data),
            errors=errors,
        )
