"""End-to-end tests against the fake Player over real HTTP and WebSocket."""

from __future__ import annotations

from ipaddress import ip_address

from homeassistant import config_entries
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import CONF_HOST, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.showpilot_player.const import CONF_PASSWORD, DOMAIN, EVENT_SHOW

from .conftest import UUID, FakePlayer, wait_for

from dataclasses import dataclass, field, replace


@dataclass
class ZeroconfServiceInfo:
    """The attributes of Home Assistant's ZeroconfServiceInfo the flow uses."""

    ip_address: object
    ip_addresses: list
    port: int
    hostname: str
    type: str
    name: str
    properties: dict = field(default_factory=dict)

    @property
    def host(self) -> str:
        return str(self.ip_address)

MP = "media_player.front_yard"


async def _setup(hass: HomeAssistant, player: FakePlayer, password: str | None = None) -> MockConfigEntry:
    data = {CONF_HOST: player.host}
    if password:
        data[CONF_PASSWORD] = password
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=UUID, title="front-yard")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------- setup
async def test_config_flow_by_address(hass: HomeAssistant, player: FakePlayer) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: player.host})
    assert result["type"] == "create_entry"
    assert result["title"] == "front-yard"
    assert result["result"].unique_id == UUID
    assert result["data"] == {CONF_HOST: player.host}
    await hass.async_block_till_done()


async def test_config_flow_with_password(hass: HomeAssistant, player: FakePlayer) -> None:
    player.password = "show-secret"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: player.host})
    assert result["step_id"] == "password"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "nope"})
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "show-secret"})
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PASSWORD] == "show-secret"
    await hass.async_block_till_done()
    assert hass.states.get(MP).state == MediaPlayerState.IDLE


async def test_config_flow_errors(hass: HomeAssistant, player: FakePlayer) -> None:
    player.state["version"] = "0.10.3"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: player.host})
    assert result["errors"] == {"base": "too_old"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "127.0.0.1:1"})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_discovered_on_the_network(hass: HomeAssistant, player: FakePlayer) -> None:
    info = ZeroconfServiceInfo(
        ip_address=ip_address("127.0.0.1"), ip_addresses=[ip_address("127.0.0.1")], port=player.port,
        hostname="front-yard.local.", type="_showpilot-player._tcp.local.",
        name="ShowPilot Player on front-yard._showpilot-player._tcp.local.",
        properties={"uuid": UUID, "version": "0.11.0", "path": "/api/player"},
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info)
    assert result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"]["name"] == "front-yard"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    # Found again at a new address: the entry follows it, no second device.
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    moved = replace(info)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=moved)
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == player.host


# ---------------------------------------------------------------- live state
async def test_entities_follow_the_show(hass: HomeAssistant, player: FakePlayer) -> None:
    await _setup(hass, player)
    assert hass.states.get(MP).state == MediaPlayerState.IDLE
    assert hass.states.get(MP).attributes["source_list"] == ["Night", "Pre-show"]
    assert hass.states.get("binary_sensor.front_yard_show_running").state == STATE_OFF
    assert hass.states.get("sensor.front_yard_status").state == "idle"
    assert hass.states.get("sensor.front_yard_next_show").state == "2026-12-01T23:30:00+00:00"
    assert hass.states.get("binary_sensor.front_yard_connected").state == STATE_ON

    events = async_capture_events(hass, EVENT_SHOW)
    await player.event("show_started", {"playlist": "Night"}, state="playing", playlist="Night")
    await player.event(
        "song_started", {"playlist": "Night", "song": "Carol of the Bells"},
        song="Carol of the Bells", next="Wizards", durationMs=185000, songStartedAt="2026-12-01T23:31:00.000Z",
    )
    await wait_for(lambda: len(events) == 2)
    await hass.async_block_till_done()
    st = hass.states.get(MP)
    assert st.state == MediaPlayerState.PLAYING
    assert st.attributes["media_title"] == "Carol of the Bells"
    assert st.attributes["source"] == "Night"
    assert st.attributes["media_duration"] == 185
    assert st.attributes["next_song"] == "Wizards"
    assert "volume_level" not in st.attributes
    assert hass.states.get("binary_sensor.front_yard_show_running").state == STATE_ON
    assert hass.states.get("sensor.front_yard_song").state == "Carol of the Bells"
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UUID)})
    assert [e.data["type"] for e in events] == ["show_started", "song_started"]
    assert events[1].data["device_id"] == device.id
    assert events[1].data["song"] == "Carol of the Bells"
    assert device.sw_version == "0.11.0"


async def test_device_trigger_runs_an_automation(hass: HomeAssistant, player: FakePlayer) -> None:
    await _setup(hass, player)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UUID)})
    from custom_components.showpilot_player.device_trigger import async_get_triggers

    types = [t["type"] for t in await async_get_triggers(hass, device.id)]
    assert types == ["show_starting", "show_started", "song_started", "show_stopping", "show_stopped", "show_resumed"]
    calls = async_capture_events(hass, "porch_lights_off")
    assert await async_setup_component(hass, "automation", {"automation": [{
        "trigger": {"platform": "device", "domain": DOMAIN, "device_id": device.id, "type": "show_starting"},
        "action": {"event": "porch_lights_off"},
    }]})
    await player.event("song_started", {"song": "x"}, state="playing")
    await player.event("show_starting", {"playlist": "Night", "source": "schedule"}, state="starting")
    await wait_for(lambda: len(calls) == 1)
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_reconnects_after_the_player_goes_away(hass: HomeAssistant, player: FakePlayer) -> None:
    await _setup(hass, player)
    await player.stop()  # Player off / rebooting
    await wait_for(lambda: hass.states.get(MP).state == STATE_UNAVAILABLE)
    assert hass.states.get("binary_sensor.front_yard_connected").state == STATE_OFF
    player.state["show"].update(state="playing", playlist="Night", song="Wizards")
    await player.start()  # back on the same port
    await wait_for(lambda: hass.states.get(MP).state == MediaPlayerState.PLAYING, timeout=5)
    assert hass.states.get("binary_sensor.front_yard_connected").state == STATE_ON
    assert hass.states.get(MP).attributes["media_title"] == "Wizards"


async def test_resumed_after_restart_fires_once(hass: HomeAssistant, player: FakePlayer) -> None:
    await _setup(hass, player)
    events = async_capture_events(hass, EVENT_SHOW)
    await player.stop()
    player.state["lastResume"] = {"at": "2026-12-01T23:40:00.000Z", "playlist": "Night", "item": "Wizards", "source": "schedule", "downSec": 41}
    await player.start()
    await wait_for(lambda: any(e.data["type"] == "show_resumed" for e in events), timeout=5)
    await player.set_show(state="playing")  # same lastResume again: no second event
    await hass.async_block_till_done()
    assert [e.data["type"] for e in events].count("show_resumed") == 1
    assert events[0].data["downSec"] == 41
    assert hass.states.get("sensor.front_yard_last_resumed").state == "2026-12-01T23:40:00+00:00"


# ---------------------------------------------------------------- control
async def test_controls_reach_the_player(hass: HomeAssistant, player: FakePlayer) -> None:
    await _setup(hass, player)
    await hass.services.async_call("media_player", "select_source", {"entity_id": MP, "source": "Night"}, blocking=True)
    await hass.services.async_call("button", "press", {"entity_id": "button.front_yard_stop_after_this_song"}, blocking=True)
    await hass.services.async_call("button", "press", {"entity_id": "button.front_yard_stop_now"}, blocking=True)
    await hass.services.async_call(DOMAIN, "insert_playlist", {"entity_id": MP, "playlist": "Pre-show", "when": "now"}, blocking=True)
    await hass.services.async_call(DOMAIN, "run_preset", {"entity_id": MP, "preset": "Lights on"}, blocking=True)
    res = await hass.services.async_call(
        DOMAIN, "run_command", {"entity_id": MP, "command": "Start Playlist", "args": ["Night", "true"]},
        blocking=True, return_response=True,
    )
    assert res[MP]["result"] == "ran Start Playlist"
    assert player.calls == [
        ("play", {"playlist": "Night", "repeat": False}),
        ("stop", {"graceful": True}),
        ("stop", {"graceful": False}),
        ("insert", {"playlist": "Pre-show", "immediate": True}),
        ("presets/run", {"name": "Lights on"}),
        ("command", {"command": "Start Playlist", "args": ["Night", "true"]}),
    ]
    # The Player's own error reaches the user.
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="playlist not found: Nope"):
        await hass.services.async_call(DOMAIN, "start_playlist", {"entity_id": MP, "playlist": "Nope"}, blocking=True)


async def test_password_session_renewed_and_changed(hass: HomeAssistant, player: FakePlayer) -> None:
    player.password = "show-secret"
    entry = await _setup(hass, player, "show-secret")
    # The Player restarted and forgot its sessions: commands log in again.
    player.sessions.clear()
    await hass.services.async_call("button", "press", {"entity_id": "button.front_yard_next_song"}, blocking=True)
    assert player.calls == [("next", {})]
    # The password changed on the Player: a reconnect can't log in -> reauth.
    player.password = "new-secret"
    player.sessions.clear()
    await player.drop_clients()
    await wait_for(lambda: any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress()), timeout=5)
    flow = next(f for f in hass.config_entries.flow.async_progress() if f["context"]["source"] == "reauth")
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_PASSWORD: "new-secret"})
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-secret"
    await hass.async_block_till_done()
    await wait_for(lambda: hass.states.get(MP).state == MediaPlayerState.IDLE)
