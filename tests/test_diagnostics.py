"""Config-entry diagnostics tests."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.venstar_acc_tsenwifi_emulator.const import DOMAIN
from custom_components.venstar_acc_tsenwifi_emulator.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import GOLDEN_MAC_PREFIX, make_sensor_config, seeded_storage

pytestmark = pytest.mark.usefixtures("broadcast_mocks")


async def test_diagnostics_report(hass: HomeAssistant, hass_storage: dict) -> None:
    hass.states.async_set("sensor.den_temp", "72.5")
    sensors = {
        "0": make_sensor_config(entity_id="sensor.den_temp", name="Den", sequence=42),
        "1": make_sensor_config(entity_id="sensor.gone", name="Gone", enabled=False),
    }
    seeded_storage(hass_storage, sensors=sensors)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac_prefix": GOLDEN_MAC_PREFIX},
        title="Venstar ACC-TSENWIFI Emulator",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["mac_prefix"] == GOLDEN_MAC_PREFIX

    den = diag["sensors"]["0"]
    assert den["name"] == "Den"
    assert den["entity_id"] == "sensor.den_temp"
    assert den["purpose"] == "Remote"
    assert den["enabled"] is True
    assert den["coordinator_running"] is True
    assert den["source_state"] == "72.5"
    assert den["source_temperature"] == 72.5
    # The coordinator broadcast on start, so a packet is cached.
    assert den["has_cached_packet"] is True

    gone = diag["sensors"]["1"]
    assert gone["enabled"] is False
    assert gone["coordinator_running"] is False
    assert gone["source_state"] is None
    assert gone["source_temperature"] is None
    assert gone["has_cached_packet"] is False

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
