"""Against a real ShowPilot Player (optional).

Runs only when SHOWPILOT_PLAYER_DIR points at a ShowPilot Player checkout
(0.11.0+, with node_modules) and node is installed; CI skips it.
  SHOWPILOT_PLAYER_DIR=../ShowPilot-Player python -m pytest tests/test_real_player.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile

import pytest

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.showpilot_player.const import DOMAIN, EVENT_SHOW

from .conftest import wait_for

PLAYER = os.environ.get("SHOWPILOT_PLAYER_DIR")
pytestmark = pytest.mark.skipif(
    not PLAYER or not shutil.which("node"), reason="set SHOWPILOT_PLAYER_DIR to a ShowPilot Player checkout"
)

SEED = """
const path = require('path');
const dir = process.argv[1];
process.env.SPP_MEDIA_DIR = process.argv[2];
const paths = require(path.join(dir, 'lib/paths')); paths.ensureLayout();
const { writeFseq } = require(path.join(dir, 'lib/fseq'));
const playlists = require(path.join(dir, 'lib/playlists'));
for (const n of ['one', 'two']) writeFseq(path.join(paths.dir('sequences'), n + '.fseq'),
  { channelCount: 30, stepMs: 25, frames: Array.from({ length: 40 * 60 }, () => Buffer.alloc(30)) });
playlists.save('Night', { mainPlaylist: ['one', 'two'].map((n) => ({ type: 'sequence', enabled: 1, sequenceName: n + '.fseq' })), repeat: 1 });
"""


class RealPlayer:
    def __init__(self) -> None:
        self.media = tempfile.mkdtemp(prefix="spp-ha-")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.proc: subprocess.Popen | None = None
        subprocess.run(["node", "-e", SEED, str(Path(PLAYER).resolve()), self.media], check=True)

    def start(self) -> None:
        env = {**os.environ, "SPP_MEDIA_DIR": self.media, "SPP_PORT": str(self.port), "SPP_AUDIO_NULL": "1",
               "SPP_AVAHI_SERVICE": os.path.join(self.media, "avahi.service"), "SPP_FPPDIR": str(Path(PLAYER, "fpp-compat").resolve())}
        self.proc = subprocess.Popen(["node", "server.js"], cwd=PLAYER, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def kill(self, sig=signal.SIGKILL) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(sig)
            self.proc.wait(10)

    async def up(self) -> None:
        async with asyncio.timeout(15):
            while True:
                try:
                    _, w = await asyncio.open_connection("127.0.0.1", self.port)
                    w.close()
                    return
                except OSError:
                    await asyncio.sleep(0.2)


@pytest.fixture
async def real(socket_enabled):
    p = RealPlayer()
    p.start()
    await p.up()
    yield p
    p.kill()
    shutil.rmtree(p.media, ignore_errors=True)


async def test_real_player_show_power_cut_and_resume(hass: HomeAssistant, real: RealPlayer) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: f"127.0.0.1:{real.port}"}, unique_id="real")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mp = next(s.entity_id for s in hass.states.async_all("media_player"))
    events = async_capture_events(hass, EVENT_SHOW)

    await hass.services.async_call("media_player", "select_source", {"entity_id": mp, "source": "Night"}, blocking=True)
    await wait_for(lambda: hass.states.get(mp).attributes.get("media_title") == "one", timeout=10)
    assert hass.states.get(mp).state == MediaPlayerState.PLAYING
    assert hass.states.get(mp).attributes["next_song"] == "two"
    assert [e.data["type"] for e in events][:2] == ["show_started", "song_started"]

    await asyncio.sleep(11)  # past the Player's 10 s position save
    real.kill()  # power cut
    await wait_for(lambda: hass.states.get(mp).state == STATE_UNAVAILABLE, timeout=10)
    real.start()
    await real.up()
    await wait_for(lambda: any(e.data["type"] == "show_resumed" for e in events), timeout=20)
    await wait_for(lambda: hass.states.get(mp).state == MediaPlayerState.PLAYING, timeout=10)
    resumed = next(e for e in events if e.data["type"] == "show_resumed")
    assert resumed.data["song"] == "one" and resumed.data["source"] == "manual"
    assert resumed.data["positionMs"] >= 7000

    await hass.services.async_call("button", "press", {"entity_id": next(
        s.entity_id for s in hass.states.async_all("button") if s.entity_id.endswith("_stop_now"))}, blocking=True)
    await wait_for(lambda: hass.states.get(mp).state == MediaPlayerState.IDLE, timeout=10)
    assert events[-1].data["type"] == "show_stopped"
    assert await hass.config_entries.async_unload(entry.entry_id)
