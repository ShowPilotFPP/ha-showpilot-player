![ShowPilot Player icon](https://raw.githubusercontent.com/ShowPilotFPP/ha-showpilot-player/main/custom_components/showpilot_player/brand/icon.png)

# ShowPilot Player for Home Assistant

Connects [ShowPilot Player](https://github.com/ShowPilotFPP/showpilot-player) to Home Assistant, so your house can react to the light show: porch lights off when the show starts, back on when it ends, a notification when a song is requested, and so on. You can also start, stop and skip from Home Assistant.

- **Live, not polled.** The Player tells Home Assistant the moment something changes over one connection. When nothing happens, nothing is sent except a small keep-alive every 30 seconds. No MQTT broker needed.
- **Found automatically.** Players on your network show up under **Settings → Devices & services** as "Discovered".
- **Needs ShowPilot Player 0.11.0 or newer.**

## Installation

### HACS

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ShowPilotFPP&repository=ha-showpilot-player&category=integration)

Click the button above, then **Download** and restart Home Assistant.

Or add it by hand:

1. In HACS, open the ⋮ menu → **Custom repositories**.
2. Add `https://github.com/ShowPilotFPP/ha-showpilot-player` with type **Integration**.
3. Install **ShowPilot Player** and restart Home Assistant.

### Manual

Copy `custom_components/showpilot_player` into your Home Assistant `config/custom_components/` folder and restart.

## Setup

After the restart the Player should appear as **Discovered** under **Settings → Devices & services**: click **Add**. If it doesn't (for example, Home Assistant is on another network), use **Add integration → ShowPilot Player** and type the Player's address (`192.0.2.20` or `showpilot-player.local`; add `:port` if it isn't on port 80).

If the Player has an admin password, you'll be asked for it. If you change the password later, Home Assistant asks for the new one. If the Player's address changes, discovery updates it by itself; or use **Reconfigure**.

## What you get

One device per Player with:

| Entity | What it shows / does |
|---|---|
| Media player | Current playlist and song, play/pause, stop (after this song), next/previous, pick a playlist to start from the source list |
| Status | idle, starting, playing, paused, stopping |
| Playlist, Song, Next song | What's playing and what's next |
| Next show, Show ends | When the next scheduled show starts and when the current one ends |
| Last resumed | When the Player last carried on a show after a restart or power cut |
| Show running | On while a show plays |
| Show hours | On during a scheduled show window |
| Background music | On while background music plays |
| Connected | Whether Home Assistant can reach the Player (stays available when the Player is down) |
| Buttons | Stop after this song, Stop now, Next song, Start Main Show, Restart Player |

There is no volume control, on purpose.

### Actions

- `showpilot_player.start_playlist` — start a playlist (optionally repeating)
- `showpilot_player.insert_playlist` — play a playlist after the current song (or now), then carry on with the show
- `showpilot_player.start_main_show` — the Player's Start Main Show (with its music fade)
- `showpilot_player.stop_after_song`
- `showpilot_player.run_preset` — run one of the Player's command presets
- `showpilot_player.run_command` — run any FPP-style command; returns the Player's answer

### Triggers

Each Player has device triggers (in the automation editor: **When → Device → your Player**):

- **Show starting** — the schedule window opened, or Start Main Show began its fade (a moment before the first song)
- **Show started**
- **Song started**
- **Show stopping** — a stop after this song / after this loop was asked for
- **Show stopped**
- **Show resumed** — the Player carried on a show after it restarted

The same events also arrive as the `showpilot_player_event` event, with `type`, `device_id`, `playlist`, `song` and more, for template automations.

## Example: porch lights off during the show

```yaml
automation:
  - alias: "Porch lights off for the show"
    triggers:
      - trigger: device
        domain: showpilot_player
        device_id: YOUR_PLAYER_DEVICE_ID
        type: show_starting
    actions:
      - action: light.turn_off
        target:
          entity_id: light.porch

  - alias: "Porch lights back on after the show"
    triggers:
      - trigger: device
        domain: showpilot_player
        device_id: YOUR_PLAYER_DEVICE_ID
        type: show_stopped
    actions:
      - action: light.turn_on
        target:
          entity_id: light.porch
```

The automation editor fills in the device ID for you when you pick the Player.

A notification when a viewer's request starts playing:

```yaml
triggers:
  - trigger: event
    event_type: showpilot_player_event
    event_data:
      type: song_started
      request: true
conditions: []
actions:
  - action: notify.notify
    data:
      message: "Request playing: {{ trigger.event.data.song }}"
```

## Development

```
pip install -r requirements_test.txt
python -m pytest
```

`tests/test_real_player.py` also runs the integration against a real ShowPilot Player (power cut included) when `SHOWPILOT_PLAYER_DIR` points at a Player checkout with its `node_modules`.
