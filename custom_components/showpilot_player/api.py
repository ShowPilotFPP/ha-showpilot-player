"""Client for a ShowPilot Player's admin API and live event feed.

HTTP: the same /api/player endpoints the Player's own web page uses.
Live state: one WebSocket at /api/player/events; the Player sends its full
state when it connects and then a message only when something changes.
Signing in, best first:
- an access key (Player 0.13.1+), got by pairing: the Player shows "Allow
  Home Assistant?" and hands over a key of its own; sent as a Bearer header;
- a user name and password (Player 0.13.0+), or only a password (older),
  for a session cookie, renewed when it expires.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

COOKIE = "spp_session"
PAIR_POLL_SECONDS = 2.0


class PlayerError(Exception):
    """The Player refused a request or answered something unexpected."""


class PlayerConnectionError(PlayerError):
    """The Player could not be reached."""


class PlayerAuthError(PlayerError):
    """The Player needs a (different) access key or password."""


class PairingNotSupported(PlayerError):
    """The Player is older than 0.13.1 and can't pair."""


class PairingDenied(PlayerError):
    """Someone pressed Deny on the Player, or the request expired."""


class PlayerClient:
    """Talk to one ShowPilot Player."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        password: str | None = None,
        timeout: float = 15,
        *,
        username: str | None = None,
        access_key: str | None = None,
    ) -> None:
        self.host = host.strip().rstrip("/")
        if self.host.startswith("http://"):
            self.host = self.host[7:]
        self._session = session
        self._password = password or None
        self._username = username or None
        self._access_key = access_key or None
        self._cookie: str | None = None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._login_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}"

    @property
    def events_url(self) -> str:
        return f"ws://{self.host}/api/player/events"

    @property
    def can_sign_in(self) -> bool:
        """Whether a 401 can be fixed by signing in again (a key can't)."""
        return bool(self._password) and not self._access_key

    def _headers(self) -> dict[str, str]:
        if self._access_key:
            return {"Authorization": f"Bearer {self._access_key}"}
        return {"Cookie": f"{COOKIE}={self._cookie}"} if self._cookie else {}

    async def _raw(
        self, method: str, path: str, json: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        url = f"{self.base_url}/api/player{path}"
        try:
            async with self._session.request(
                method, url, json=json, headers=headers if headers is not None else self._headers(), timeout=self._timeout
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    data = None
                if path == "/auth/login" and resp.status == 200:
                    morsel = resp.cookies.get(COOKIE)
                    if morsel is not None:
                        self._cookie = morsel.value
                return resp.status, data
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise PlayerConnectionError(f"{method} {path} on {self.host} failed: {err}") from err

    async def auth_info(self) -> dict[str, Any]:
        """What the Player says about signing in (/auth)."""
        status, data = await self._raw("GET", "/auth", headers={})
        if status != 200 or not isinstance(data, dict):
            raise PlayerError(f"{self.host} is not a ShowPilot Player (GET /auth: {status})")
        return data

    async def auth_required(self) -> bool:
        """Whether the Player needs a sign-in."""
        return bool((await self.auth_info()).get("required"))

    # ------------------------------------------------------------ pairing (Player 0.13.1+)
    async def pair_request(self, name: str) -> dict[str, Any]:
        """Ask for an access key: {id, code, poll}. Someone allows it on the Player."""
        status, data = await self._raw("POST", "/pair/request", {"name": name}, headers={})
        if status == 404:
            raise PairingNotSupported(f"{self.host} can't pair (ShowPilot Player older than 0.13.1)")
        if status >= 400 or not isinstance(data, dict) or "id" not in data:
            msg = data.get("error") if isinstance(data, dict) else None
            raise PlayerError(msg or f"pairing request on {self.host}: HTTP {status}")
        return data

    async def pair_poll(self, request_id: str, secret: str) -> dict[str, Any]:
        """{state: pending|approved|denied|expired, key?}."""
        status, data = await self._raw(
            "GET", f"/pair/request/{request_id}", headers={"X-SPP-Pair-Secret": secret}
        )
        if status >= 400 or not isinstance(data, dict):
            msg = data.get("error") if isinstance(data, dict) else None
            raise PlayerError(msg or f"pairing on {self.host}: HTTP {status}")
        return data

    async def wait_for_pairing(self, request_id: str, secret: str) -> str:
        """Poll until someone allows (→ the key) or denies (PairingDenied)."""
        while True:
            try:
                data = await self.pair_poll(request_id, secret)
            except PlayerConnectionError:
                data = {"state": "pending"}  # a blip while waiting for a person
            state = data.get("state")
            if state == "approved" and data.get("key"):
                self._access_key = data["key"]
                return data["key"]
            if state in ("denied", "expired"):
                raise PairingDenied(state)
            await asyncio.sleep(PAIR_POLL_SECONDS)

    async def login(self) -> None:
        if not self._password:
            raise PlayerAuthError("this Player has an admin password")
        async with self._login_lock:
            body = {"password": self._password}
            if self._username:
                body["username"] = self._username
            status, data = await self._raw("POST", "/auth/login", body, headers={})
        if status == 200:
            return
        if status in (401, 429):
            raise PlayerAuthError((data or {}).get("error") or "wrong password")
        raise PlayerError(f"login on {self.host} failed: HTTP {status}")

    async def request(self, method: str, path: str, json: Any = None) -> Any:
        status, data = await self._raw(method, path, json)
        if status == 401 and self.can_sign_in:
            await self.login()
            status, data = await self._raw(method, path, json)
        if status == 401:
            raise PlayerAuthError("the Player refused the access key or password")
        if status >= 400:
            msg = data.get("error") if isinstance(data, dict) else None
            raise PlayerError(msg or f"{method} {path}: HTTP {status}")
        return data

    # ------------------------------------------------------------ reads
    async def status(self) -> dict[str, Any]:
        data = await self.request("GET", "/status")
        if not isinstance(data, dict) or "version" not in data:
            raise PlayerError(f"{self.host} answered /status with something unexpected")
        return data

    # ------------------------------------------------------------ show control
    async def play_playlist(self, name: str, repeat: bool = False) -> None:
        await self.request("POST", "/play", {"playlist": name, "repeat": repeat})

    async def insert_playlist(self, name: str, immediate: bool) -> None:
        await self.request("POST", "/insert", {"playlist": name, "immediate": immediate})

    async def stop(self, graceful: bool = False) -> None:
        await self.request("POST", "/stop", {"graceful": graceful})

    async def next(self) -> None:
        await self.request("POST", "/next", {})

    async def previous(self) -> None:
        await self.request("POST", "/prev", {})

    async def toggle_pause(self) -> None:
        await self.request("POST", "/pause", {})

    async def start_main_show(self) -> None:
        await self.request("POST", "/music/main-show", {})

    async def run_command(self, command: str, args: list[str]) -> Any:
        return await self.request("POST", "/command", {"command": command, "args": args})

    async def run_preset(self, name: str) -> Any:
        return await self.request("POST", "/presets/run", {"name": name})

    async def restart(self) -> None:
        await self.request("POST", "/system/restart", {})

    # ------------------------------------------------------------ live feed
    async def connect_events(self) -> aiohttp.ClientWebSocketResponse:
        """Open the live event feed (logging in first when needed)."""
        for attempt in range(2):
            try:
                return await self._session.ws_connect(
                    self.events_url,
                    headers=self._headers(),
                    heartbeat=None,  # the Player pings; aiohttp answers
                    autoping=True,
                    timeout=aiohttp.ClientWSTimeout(ws_close=10),
                )
            except aiohttp.WSServerHandshakeError as err:
                if err.status == 401 and attempt == 0 and self.can_sign_in:
                    await self.login()
                    continue
                if err.status == 401:
                    raise PlayerAuthError("the Player refused the access key or password") from err
                if err.status == 404:
                    raise PlayerError(
                        f"{self.host} has no live event feed: update ShowPilot Player to 0.11.0 or newer"
                    ) from err
                raise PlayerConnectionError(f"event feed on {self.host}: HTTP {err.status}") from err
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise PlayerConnectionError(f"event feed on {self.host} failed: {err}") from err
        raise PlayerAuthError("login did not give access to the event feed")
