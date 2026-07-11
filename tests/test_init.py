"""Full-integration tests for setup, unload, and services."""
from __future__ import annotations

import base64
import hashlib

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.venstar_acc_tsenwifi_emulator.const import DOMAIN
from custom_components.venstar_acc_tsenwifi_emulator.protobuf.sensor_message_pb2 import (
    SensorMessage,
)

from .conftest import GOLDEN_MAC_PREFIX, make_sensor_config, seeded_storage

_INIT_BROADCAST = (
    "custom_components.venstar_acc_tsenwifi_emulator.broadcast_udp_packet"
)
_COORD_BROADCAST = (
    "custom_components.venstar_acc_tsenwifi_emulator.coordinator.broadcast_udp_packet"
)

pytestmark = pytest.mark.usefixtures("broadcast_mocks")


def _signature_key(sensor_id: int, mac_prefix: str = GOLDEN_MAC_PREFIX) -> str:
    mac = f"{mac_prefix}{sensor_id:02x}".lower()
    return base64.b64encode(hashlib.sha256(mac.encode()).digest()).decode()


async def _setup_entry(
    hass: HomeAssistant, hass_storage: dict, sensors: dict
) -> MockConfigEntry:
    seeded_storage(hass_storage, sensors=sensors)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac_prefix": GOLDEN_MAC_PREFIX},
        title="Venstar ACC-TSENWIFI Emulator",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_starts_only_enabled_coordinators(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    sensors = {
        "0": make_sensor_config(entity_id="sensor.a", name="A", enabled=True),
        "1": make_sensor_config(entity_id="sensor.b", name="B", enabled=True),
        "2": make_sensor_config(entity_id="sensor.c", name="C", enabled=False),
    }
    entry = await _setup_entry(hass, hass_storage, sensors)

    coordinators = entry.runtime_data.coordinators
    assert set(coordinators.keys()) == {0, 1}
    assert hass.services.has_service(DOMAIN, "pair_sensor")
    assert hass.services.has_service(DOMAIN, "resend_last_packet")

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_pair_sensor_service(
    hass: HomeAssistant, hass_storage: dict, broadcast_mocks
) -> None:
    init_bc, _ = broadcast_mocks
    hass.states.async_set("sensor.temp", "68.0")
    sensors = {
        "0": make_sensor_config(entity_id="sensor.temp", scale="F", sequence=500)
    }
    entry = await _setup_entry(hass, hass_storage, sensors)
    init_bc.reset_mock()

    await hass.services.async_call(
        DOMAIN, "pair_sensor", {"sensor_id": 0}, blocking=True
    )

    init_bc.assert_called_once()
    packet = init_bc.call_args.args[0]
    message = SensorMessage.FromString(packet)
    assert message.Command == SensorMessage.SENSORPAIR
    assert message.SensorData.Signature == _signature_key(0)
    # Sequence reset to 1 (was 500).
    assert entry.runtime_data.storage.get_sensor(0)["sequence"] == 1

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_pair_sensor_unconfigured_id(
    hass: HomeAssistant,
    hass_storage: dict,
    broadcast_mocks,
    caplog: pytest.LogCaptureFixture,
) -> None:
    init_bc, _ = broadcast_mocks
    entry = await _setup_entry(hass, hass_storage, {})
    init_bc.reset_mock()

    # sensor_id 3 is valid per schema but not configured.
    await hass.services.async_call(
        DOMAIN, "pair_sensor", {"sensor_id": 3}, blocking=True
    )
    init_bc.assert_not_called()
    assert "not configured" in caplog.text

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_pair_sensor_entity_unavailable(
    hass: HomeAssistant, hass_storage: dict, broadcast_mocks
) -> None:
    init_bc, _ = broadcast_mocks
    hass.states.async_set("sensor.temp", "unavailable")
    sensors = {"0": make_sensor_config(entity_id="sensor.temp")}
    entry = await _setup_entry(hass, hass_storage, sensors)
    init_bc.reset_mock()

    await hass.services.async_call(
        DOMAIN, "pair_sensor", {"sensor_id": 0}, blocking=True
    )
    init_bc.assert_not_called()

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("bad_id", [20, -1], ids=["too-high", "too-low"])
async def test_pair_sensor_schema_rejects_out_of_range(
    hass: HomeAssistant, hass_storage: dict, bad_id: int
) -> None:
    entry = await _setup_entry(hass, hass_storage, {"0": make_sensor_config()})

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "pair_sensor", {"sensor_id": bad_id}, blocking=True
        )

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_pair_sensor_schema_coerces_string_id(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    hass.states.async_set("sensor.temp", "68.0")
    sensors = {"0": make_sensor_config(entity_id="sensor.temp")}
    entry = await _setup_entry(hass, hass_storage, sensors)

    # String "0" should coerce to int without a schema error.
    await hass.services.async_call(
        DOMAIN, "pair_sensor", {"sensor_id": "0"}, blocking=True
    )

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_resend_last_packet_rebroadcasts_identical_bytes(
    hass: HomeAssistant, hass_storage: dict, broadcast_mocks
) -> None:
    init_bc, _ = broadcast_mocks
    hass.states.async_set("sensor.temp", "72.0")
    sensors = {
        "0": make_sensor_config(entity_id="sensor.temp", scale="F", sequence=10)
    }
    entry = await _setup_entry(hass, hass_storage, sensors)
    await hass.async_block_till_done()

    storage = entry.runtime_data.storage
    cached = storage.get_last_packet(0)
    assert cached is not None  # coordinator broadcast on start cached it
    seq_before = storage.get_sensor(0)["sequence"]

    init_bc.reset_mock()
    await hass.services.async_call(
        DOMAIN, "resend_last_packet", {"sensor_id": 0}, blocking=True
    )

    init_bc.assert_called_once_with(cached)
    # Sequence untouched by a resend.
    assert storage.get_sensor(0)["sequence"] == seq_before

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_resend_last_packet_never_broadcast(
    hass: HomeAssistant,
    hass_storage: dict,
    broadcast_mocks,
    caplog: pytest.LogCaptureFixture,
) -> None:
    init_bc, _ = broadcast_mocks
    # Disabled sensor: no coordinator, so it never broadcasts / caches a packet.
    sensors = {"0": make_sensor_config(enabled=False)}
    entry = await _setup_entry(hass, hass_storage, sensors)
    init_bc.reset_mock()

    await hass.services.async_call(
        DOMAIN, "resend_last_packet", {"sensor_id": 0}, blocking=True
    )
    init_bc.assert_not_called()
    assert "no cached packet" in caplog.text

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    "entry_data",
    [{}, {"mac_prefix": "aaaaaaaaaa"}],
    ids=["missing-key", "stale-prefix"],
)
async def test_setup_backfills_mac_prefix_into_entry_data(
    hass: HomeAssistant, hass_storage: dict, entry_data: dict
) -> None:
    """entry.data must end up matching storage: the companion listener
    integration's "ignore locally-emulated sensors" filter reads it from there.
    """
    seeded_storage(hass_storage, sensors={})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        title="Venstar ACC-TSENWIFI Emulator",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.data["mac_prefix"] == GOLDEN_MAC_PREFIX
    # Storage (the broadcast source of truth) is untouched.
    assert entry.runtime_data.storage.mac_prefix == GOLDEN_MAC_PREFIX

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_unload_stops_coordinators_and_removes_services(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    sensors = {
        "0": make_sensor_config(entity_id="sensor.a", name="A"),
        "1": make_sensor_config(entity_id="sensor.b", name="B"),
    }
    entry = await _setup_entry(hass, hass_storage, sensors)
    coordinators = entry.runtime_data.coordinators
    assert set(coordinators.keys()) == {0, 1}

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert coordinators == {}
    assert not hass.services.has_service(DOMAIN, "pair_sensor")
    assert not hass.services.has_service(DOMAIN, "resend_last_packet")
