"""Constants for the ShowPilot Player integration."""

from __future__ import annotations

DOMAIN = "showpilot_player"
VERSION = "0.1.0"

CONF_PASSWORD = "password"

# Fired on the event bus for every show event the Player sends:
#   event_type: showpilot_player_event
#   data: {"device_id", "type", "playlist", "song", ...}
EVENT_SHOW = f"{DOMAIN}_event"

# Show events the Player sends (lib/events.js), in the order they happen.
EVENT_TYPES = [
    "show_starting",  # schedule window opened / Start Main Show began its fade
    "show_started",
    "song_started",
    "show_stopping",  # graceful stop requested (after this song / after this loop)
    "show_stopped",
    "show_resumed",  # carried on after the Player restarted
]

# Show states (the Status sensor's options).
SHOW_STATES = ["idle", "starting", "playing", "paused", "stopping"]

# Reconnect back-off (seconds): quick at first, then every 30 s.
RECONNECT_DELAYS = [1, 2, 5, 10, 30]
# The Player pings every 30 s; this long without any message means the
# connection is dead even if TCP hasn't noticed.
RECEIVE_TIMEOUT = 75
