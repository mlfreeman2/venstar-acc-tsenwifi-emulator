"""Config-entry diagnostics for the Venstar ACC-TSENWIFI Emulator.

The first thing to ask for in a "my thermostat doesn't see the sensor" issue:
the full sensor roster, whether each broadcast coordinator is actually running,
and what the source entity currently reports.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from .coordinator import extract_temperature

if TYPE_CHECKING:
    from . import VenstarConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VenstarConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data
    storage = data.storage

    sensors: dict[str, Any] = {}
    for sensor_id, config in storage.sensors.items():
        state = hass.states.get(config["entity_id"])
        sensors[sensor_id] = {
            "name": config["name"],
            "entity_id": config["entity_id"],
            "purpose": config["purpose"],
            "scale": config.get("scale", "F"),
            "enabled": config.get("enabled", True),
            "sequence": config.get("sequence"),
            # A sensor that has never broadcast has nothing cached to resend.
            "has_cached_packet": config.get("last_packet") is not None,
            "coordinator_running": int(sensor_id) in data.coordinators,
            "source_state": state.state if state else None,
            "source_temperature": extract_temperature(state),
        }

    return {
        # Broadcast on the local network in every packet, so not sensitive.
        "mac_prefix": storage.mac_prefix,
        "sensors": sensors,
    }
