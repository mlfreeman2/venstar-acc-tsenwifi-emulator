"""Tests for VenstarEmulatorStorage (uses hass + hass_storage)."""
from __future__ import annotations

import base64
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed
import homeassistant.util.dt as dt_util

from custom_components.venstar_acc_tsenwifi_emulator.const import STORAGE_KEY
from custom_components.venstar_acc_tsenwifi_emulator.storage import (
    SAVE_DELAY,
    VenstarEmulatorStorage,
)

from .conftest import GOLDEN_MAC_PREFIX, make_sensor_config, seeded_storage


async def test_load_fresh_uses_provided_mac_prefix(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)

    assert storage.mac_prefix == GOLDEN_MAC_PREFIX
    assert storage.sensors == {}
    # Persisted immediately on first load.
    assert hass_storage[STORAGE_KEY]["data"]["mac_prefix"] == GOLDEN_MAC_PREFIX


async def test_load_fresh_generates_mac_prefix(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load()

    assert storage.mac_prefix is not None
    assert len(storage.mac_prefix) == 10
    assert all(c in "0123456789abcdef" for c in storage.mac_prefix)
    assert hass_storage[STORAGE_KEY]["data"]["mac_prefix"] == storage.mac_prefix


async def test_load_existing_roundtrips(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    sensors = {"0": make_sensor_config(name="Sensor A")}
    seeded_storage(hass_storage, sensors=sensors, mac_prefix="aabbccddee")

    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)

    # Existing prefix wins over the passed-in one.
    assert storage.mac_prefix == "aabbccddee"
    assert storage.sensors == sensors


async def test_add_sensor_assigns_sequential_ids(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)

    assert storage.add_sensor("sensor.a", "A", "Remote") == 0
    assert storage.add_sensor("sensor.b", "B", "Remote") == 1
    assert storage.add_sensor("sensor.c", "C", "Remote") == 2
    assert storage.sensors["0"]["sequence"] == 1


async def test_add_sensor_fills_gaps(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)

    storage.add_sensor("sensor.a", "A", "Remote")  # id 0
    storage.add_sensor("sensor.b", "B", "Remote")  # id 1
    storage.delete_sensor(0)
    # Next add reuses the freed id 0.
    assert storage.add_sensor("sensor.c", "C", "Remote") == 0


async def test_add_sensor_duplicate_name_raises(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)

    storage.add_sensor("sensor.a", "Dup", "Remote")
    with pytest.raises(ValueError):
        storage.add_sensor("sensor.b", "Dup", "Remote")


async def test_add_sensor_max_reached_raises(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)

    for i in range(20):
        storage.add_sensor(f"sensor.s{i}", f"Sensor {i}", "Remote")
    with pytest.raises(ValueError):
        storage.add_sensor("sensor.overflow", "Overflow", "Remote")


async def test_update_sensor_partial_leaves_other_fields(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    storage.add_sensor("sensor.a", "A", "Remote", scale="F", enabled=True)

    storage.update_sensor(0, name="Renamed")
    config = storage.get_sensor(0)
    assert config["name"] == "Renamed"
    assert config["entity_id"] == "sensor.a"
    assert config["purpose"] == "Remote"
    assert config["scale"] == "F"
    assert config["enabled"] is True


async def test_update_sensor_rename_conflict_raises(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    storage.add_sensor("sensor.a", "A", "Remote")
    storage.add_sensor("sensor.b", "B", "Remote")

    with pytest.raises(ValueError):
        storage.update_sensor(1, name="A")


async def test_update_sensor_keep_own_name_allowed(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    storage.add_sensor("sensor.a", "A", "Remote")

    storage.update_sensor(0, name="A", purpose="Outdoor")
    assert storage.get_sensor(0)["purpose"] == "Outdoor"


async def test_update_sensor_unknown_id_raises(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    with pytest.raises(ValueError):
        storage.update_sensor(0, name="Nope")


async def test_delete_sensor(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    storage.add_sensor("sensor.a", "A", "Remote")

    storage.delete_sensor(0)
    assert storage.get_sensor(0) is None
    with pytest.raises(ValueError):
        storage.delete_sensor(0)


async def test_update_sequence(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    storage.add_sensor("sensor.a", "A", "Remote")

    storage.update_sequence(0, 500)
    assert storage.get_sensor(0)["sequence"] == 500
    with pytest.raises(ValueError):
        storage.update_sequence(99, 1)


async def test_update_and_get_last_packet_roundtrip(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    storage.add_sensor("sensor.a", "A", "Remote")

    packet = b"\x00\x01\x02\xff"
    storage.update_last_packet(0, packet)
    assert (
        storage.get_sensor(0)["last_packet"]
        == base64.b64encode(packet).decode("utf-8")
    )
    assert storage.get_last_packet(0) == packet


async def test_update_last_packet_unknown_id_raises(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    with pytest.raises(ValueError):
        storage.update_last_packet(0, b"x")


async def test_get_last_packet_none_cases(hass: HomeAssistant) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    storage.add_sensor("sensor.a", "A", "Remote")

    # Unknown sensor.
    assert storage.get_last_packet(99) is None
    # Sensor that never broadcast.
    assert storage.get_last_packet(0) is None


async def test_async_save_immediate_writes_synchronously(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    storage.add_sensor("sensor.a", "A", "Remote")

    await storage.async_save(immediate=True)
    assert "0" in hass_storage[STORAGE_KEY]["data"]["sensors"]


async def test_async_save_delayed_lands_after_delay(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    storage.add_sensor("sensor.a", "A", "Remote")
    await storage.async_save(immediate=True)

    # Mutate and request a delayed (non-immediate) save.
    storage.update_sequence(0, 777)
    await storage.async_save(immediate=False)

    # Not written yet.
    assert hass_storage[STORAGE_KEY]["data"]["sensors"]["0"]["sequence"] == 1

    # Store.async_delay_save is HA-timer based — advancing time flushes it.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=SAVE_DELAY + 1))
    await hass.async_block_till_done()

    assert hass_storage[STORAGE_KEY]["data"]["sensors"]["0"]["sequence"] == 777
