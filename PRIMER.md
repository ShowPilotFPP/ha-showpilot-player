# ShowPilot Player — Home Assistant Integration Primer

**Status: v0.1.0.** First release. Covered by tests in Home Assistant's own test harness (fake Player + a real ShowPilot Player 0.11.0 including a power cut); not yet installed on a production Home Assistant.

## What it is

A Home Assistant custom integration (HACS, domain `showpilot_player`) for ShowPilot Player. Its job is to let home automations react to the show (lights off when the show starts, on when it stops) and to control the show from Home Assistant. Separate repo `ShowPilotFPP/ha-showpilot-player`; the Player side lives in the Player repo (`lib/events.js`, `lib/mdns.js`, see its primer).

### Design rules (do not regress)
- **No MQTT.** A real integration talking to the Player directly.
- **Minimal network traffic.** Push only: one WebSocket per Player, the Player sends only on a change, ping every 30 s. Nothing is polled (`update_interval=None`).
- **No alerts / notifications built in.** The owner has alerts elsewhere; the integration only exposes events and states.
- **No volume control** (ShowPilot-wide rule).

## Architecture

```
custom_components/showpilot_player/
  manifest.json      local_push, zeroconf _showpilot-player._tcp.local., integration_type device
  const.py           DOMAIN, EVENT_SHOW = showpilot_player_event, EVENT_TYPES, SHOW_STATES, RECONNECT_DELAYS [1,2,5,10,30], RECEIVE_TIMEOUT 75 s
  api.py             PlayerClient: HTTP API + login (spp_session cookie, re-login once on 401) + connect_events() (WebSocket /api/player/events; 404 → "update the Player to 0.11.0")
  coordinator.py     PlayerCoordinator: first state at setup, then a background task keeps the feed open, reconnects with back-off
  entity.py          base entity (device info, available = connected)
  media_player.py    playlist/song, play/pause/stop/next/previous, source = playlist; entity services
  sensor.py          status (enum), playlist, song, next_song, next_show, show_ends, last_resumed
  binary_sensor.py   show_running, show_hours, background_music, connected (always available)
  button.py          stop_after_song, stop_now, next_song, start_main_show, restart
  device_trigger.py  device triggers = EVENT_TYPES on the bus event
  config_flow.py     user / password / zeroconf (+confirm) / reauth / reconfigure; MIN_PLAYER 0.11.0; unique_id = Player uuid
  services.yaml, strings.json, translations/en.json, brand/ (icon from the Player's favicon)
tests/               conftest.py (aiohttp FakePlayer), test_integration.py, test_real_player.py (skipped unless SHOWPILOT_PLAYER_DIR)
```

### Connection
- Setup: `_async_update_data` opens the feed and takes the first `{type:"state"}` message; auth failure → `ConfigEntryAuthFailed` (reauth), other failures → `UpdateFailed` (HA retries setup).
- Then `_run()`: consume messages (`receive(timeout=75)`); on close/error mark disconnected (`async_set_update_error`, entities unavailable except Connected), wait back-off, reconnect. `self._ws` is cleared in `finally` and a closed ws is never reused (was an endless loop). On reconnect, `_mark_connected()` runs **before** `async_set_updated_data` so Connected turns on in the same update.
- Auth lost while running → `async_start_reauth`.

### Events
- Player messages `{type:"event", event, data, state}` are fired as `showpilot_player_event` with `device_id`, `type` and the data (playlist, song, sequence, media, request, resumed, how, source…).
- `show_resumed` is not sent by the Player (it resumes at boot, before anyone is connected): the coordinator fires it when `state.lastResume.at` changes (also on the first state after a reconnect; at setup only if under 120 s old). Data: `at, playlist, song, item (file), positionMs, source schedule|manual, downSec`.

### Player state used
`host, uuid, version, show {state, playlist, song, next, index, size, durationMs, songStartedAt, request}, schedule {enabled, active, next}, music {state, title}, playlists, lastResume`. Media position = extrapolated from `songStartedAt` (no polling).

### Commands (HTTP, admin API)
Start/insert playlist, stop (graceful or now), next, previous, toggle pause, Start Main Show, run command (returns the Player's answer), run preset, restart. Results come back over the feed, so commands don't refresh anything themselves.

## Testing
- `pip install -r requirements_test.txt` (pytest-homeassistant-custom-component 0.13.205 = HA 2025.1.4), `python -m pytest`.
- `SHOWPILOT_PLAYER_DIR=<Player checkout with node_modules> python -m pytest tests/test_real_player.py`: seeds two sequences + a playlist in a temp media folder, starts the real Player (`SPP_AUDIO_NULL=1`, temp `SPP_AVAHI_SERVICE`), starts the playlist from HA, kill -9 after 11 s, restarts it, expects `show_resumed` (song "one", source manual, position ≥ 7 s), playing again, then Stop now → idle + `show_stopped`.
- CI: `.github/workflows/validate.yml` (hassfest, HACS validation, tests) on GitHub-hosted runners.
- Note: the `zeroconf` library isn't in the test venv, so `ZeroconfServiceInfo` is imported only under `TYPE_CHECKING`; the tests use their own small dataclass.

## Shipping
ShipPilot tarball, top folder `ha-showpilot-player`, `.release.json` `{repo: "ha-showpilot-player", version, tag, commit_message}`. Bump `manifest.json` version and `const.VERSION` together. HACS takes releases from the GitHub release tags.

## Ideas / open
- Optional low-traffic controller health (from the Player's own output stats, carried on the same feed) — only if it adds no polling.
- Dashboard card.

## Version history
| Version | Changes | Tested |
|---|---|---|
| 0.1.0 | First release: discovery, live state, media player, sensors, buttons, device triggers (show starting/started/song/stopping/stopped/resumed), actions (start/insert playlist, presets, commands, Start Main Show), reauth/reconfigure. Requires ShowPilot Player 0.11.0. | 10 tests against a fake Player + the real-Player test (show, power cut, resume event, stop). |
