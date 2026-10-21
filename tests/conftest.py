"""Fixtures: a fake ShowPilot Player (HTTP API + live event feed) on localhost."""

from __future__ import annotations

import asyncio
import copy
import json
import secrets
from unittest.mock import patch

from aiohttp import WSMsgType, web
import pytest

UUID = "SP-0123456789ABCDEF"


def _state() -> dict:
    return {
        "host": "front-yard",
        "uuid": UUID,
        "version": "0.11.0",
        "mode": "player",
        "ips": ["127.0.0.1"],
        "show": {
            "state": "idle", "playlist": None, "song": None, "sequence": None, "media": None,
            "request": False, "index": 0, "size": 0, "next": None, "durationMs": 0, "songStartedAt": None,
        },
        "schedule": {"enabled": True, "active": None, "next": {"name": "Night", "start": "2026-12-01T23:30:00.000Z", "end": "2026-12-02T03:00:00.000Z"}},
        "music": {"state": "off", "title": None},
        "playlists": ["Night", "Pre-show"],
        "lastResume": None,
    }


class FakePlayer:
    """Enough of a ShowPilot Player for the integration."""

    def __init__(self) -> None:
        self.state = _state()
        self.password: str | None = None
        self.sessions: set[str] = set()
        self.calls: list[tuple[str, dict]] = []
        self.clients: set[web.WebSocketResponse] = set()
        self.runner: web.AppRunner | None = None
        self.port: int | None = None
        self.refuse_ws = False

    # ---------------------------------------------------------- helpers
    def authorized(self, request: web.Request) -> bool:
        return not self.password or request.cookies.get("spp_session") in self.sessions

    async def send(self, msg: dict) -> None:
        for ws in list(self.clients):
            await ws.send_str(json.dumps(msg))

    async def set_show(self, **show) -> None:
        self.state["show"].update(show)
        await self.send({"type": "state", "state": copy.deepcopy(self.state)})

    async def event(self, event: str, data: dict | None = None, **show) -> None:
        self.state["show"].update(show)
        await self.send({"type": "event", "event": event, "data": data or {}, "state": copy.deepcopy(self.state)})

    async def drop_clients(self) -> None:
        for ws in list(self.clients):
            await ws.close()

    # ---------------------------------------------------------- server
    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/api/player/auth", self.h_auth)
        r.add_post("/api/player/auth/login", self.h_login)
        r.add_get("/api/player/status", self.h_status)
        r.add_get("/api/player/events", self.h_events)
        for p in ("play", "insert", "stop", "next", "prev", "pause", "command", "presets/run", "music/main-show", "system/restart"):
            r.add_post(f"/api/player/{p}", self.h_cmd)
        return app

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app())
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", self.port or 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        await self.drop_clients()
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.port}"

    async def h_auth(self, request):
        return web.json_response({"required": bool(self.password), "loggedIn": self.authorized(request)})

    async def h_login(self, request):
        body = await request.json()
        if self.password and body.get("password") != self.password:
            return web.json_response({"error": "wrong password"}, status=401)
        token = secrets.token_hex(8)
        self.sessions.add(token)
        resp = web.json_response({"ok": True})
        resp.set_cookie("spp_session", token, httponly=True)
        return resp

    async def h_status(self, request):
        if not self.authorized(request):
            return web.json_response({"error": "login required"}, status=401)
        return web.json_response({"version": self.state["version"], "hostName": self.state["host"], "uuid": self.state["uuid"]})

    async def h_cmd(self, request):
        if not self.authorized(request):
            return web.json_response({"error": "login required"}, status=401)
        path = request.path.removeprefix("/api/player/")
        body = await request.json()
        self.calls.append((path, body))
        if path == "play" and body.get("playlist") not in self.state["playlists"]:
            return web.json_response({"error": f"playlist not found: {body.get('playlist')}"}, status=400)
        if path == "command":
            return web.json_response({"result": f"ran {body['command']}"})
        return web.json_response({"ok": True})

    async def h_events(self, request):
        if self.refuse_ws or not self.authorized(request):
            return web.json_response({"error": "login required"}, status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.add(ws)
        await ws.send_str(json.dumps({"type": "state", "state": copy.deepcopy(self.state)}))
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
        self.clients.discard(ws)
        return ws


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def fast_reconnect():
    with patch("custom_components.showpilot_player.coordinator.RECONNECT_DELAYS", [0.05]):
        yield


@pytest.fixture
async def player(socket_enabled):
    fake = FakePlayer()
    await fake.start()
    yield fake
    await fake.stop()


async def wait_for(predicate, timeout: float = 3) -> None:
    """Let the event loop run until predicate() is true."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)
