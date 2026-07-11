"""Tests for the config and options flows."""
from __future__ import annotations

import base64
import hashlib
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.venstar_acc_tsenwifi_emulator.const import DOMAIN
from custom_components.venstar_acc_tsenwifi_emulator.protobuf.sensor_message_pb2 import (
    SensorMessage,
)

from .conftest import GOLDEN_MAC_PREFIX, make_sensor_config, seeded_storage

_CF_BROADCAST = (
    "custom_components.venstar_acc_tsenwifi_emulator.config_flow.broadcast_udp_packet"
)


@pytest.fixture
def patched_broadcasts():
    """Patch broadcast at every point of use touched by the flows."""
    with patch(
        "custom_components.venstar_acc_tsenwifi_emulator.coordinator.broadcast_udp_packet"
    ) as coord_bc, patch(_CF_BROADCAST) as cf_bc:
        yield coord_bc, cf_bc


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


# --------------------------------------------------------------------------- #
# Config flow
# --------------------------------------------------------------------------- #
async def test_config_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Venstar ACC-TSENWIFI Emulator"

    mac_prefix = result["data"]["mac_prefix"]
    assert len(mac_prefix) == 10
    assert all(c in "0123456789abcdef" for c in mac_prefix)


async def test_config_flow_single_instance(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, data={"mac_prefix": GOLDEN_MAC_PREFIX}).add_to_hass(
        hass
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


# --------------------------------------------------------------------------- #
# Options flow — menu
# --------------------------------------------------------------------------- #
async def test_options_menu_no_sensors(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(hass, hass_storage, {})
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == FlowResultType.MENU
    assert set(result["menu_options"]) == {"add_sensor", "done"}


async def test_options_menu_with_sensors(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(
        hass, hass_storage, {"0": make_sensor_config(enabled=False)}
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == FlowResultType.MENU
    assert set(result["menu_options"]) == {
        "add_sensor",
        "select_sensor_to_edit",
        "select_sensor_to_delete",
        "done",
    }


# --------------------------------------------------------------------------- #
# Options flow — add_sensor
# --------------------------------------------------------------------------- #
async def _open_add_sensor(hass: HomeAssistant, entry: MockConfigEntry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_sensor"}
    )


async def test_add_sensor_happy_path_starts_coordinator(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(hass, hass_storage, {})
    result = await _open_add_sensor(hass, entry)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "entity_id": "sensor.temp",
            "name": "New Sensor",
            "purpose": "Remote",
            "scale": "F",
            "enabled": True,
        },
    )
    assert result["type"] == FlowResultType.MENU  # back to sensor_list

    storage = entry.runtime_data.storage
    assert storage.get_sensor(0)["name"] == "New Sensor"
    # Immediate save persisted.
    assert "0" in hass_storage[
        "venstar_acc_tsenwifi_emulator"
    ]["data"]["sensors"]
    # Coordinator started for the enabled sensor.
    assert 0 in entry.runtime_data.coordinators

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_add_sensor_disabled_no_coordinator(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(hass, hass_storage, {})
    result = await _open_add_sensor(hass, entry)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "entity_id": "sensor.temp",
            "name": "Disabled",
            "purpose": "Remote",
            "scale": "F",
            "enabled": False,
        },
    )
    assert result["type"] == FlowResultType.MENU
    assert entry.runtime_data.storage.get_sensor(0) is not None
    assert 0 not in entry.runtime_data.coordinators


@pytest.mark.parametrize(
    ("name", "expected_error"),
    [
        pytest.param("X" * 15, "name_too_long", id="too-long"),
        pytest.param("   ", "name_required", id="whitespace-only"),
    ],
)
async def test_add_sensor_name_validation(
    hass: HomeAssistant,
    hass_storage: dict,
    patched_broadcasts,
    name: str,
    expected_error: str,
) -> None:
    entry = await _setup_entry(hass, hass_storage, {})
    result = await _open_add_sensor(hass, entry)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "entity_id": "sensor.temp",
            "name": name,
            "purpose": "Remote",
            "scale": "F",
            "enabled": True,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"name": expected_error}


async def test_add_sensor_duplicate_name(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(
        hass, hass_storage, {"0": make_sensor_config(name="Dup", enabled=False)}
    )
    result = await _open_add_sensor(hass, entry)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "entity_id": "sensor.temp",
            "name": "Dup",
            "purpose": "Remote",
            "scale": "F",
            "enabled": True,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"name": "name_duplicate"}


async def test_add_sensor_max_reached_aborts(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    sensors = {
        str(i): make_sensor_config(name=f"S{i}", enabled=False) for i in range(20)
    }
    entry = await _setup_entry(hass, hass_storage, sensors)

    result = await _open_add_sensor(hass, entry)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "max_sensors_reached"


# --------------------------------------------------------------------------- #
# Options flow — edit_sensor
# --------------------------------------------------------------------------- #
async def _open_edit_sensor(hass: HomeAssistant, entry: MockConfigEntry, sensor_id: int):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "select_sensor_to_edit"}
    )
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"sensor_id": str(sensor_id)}
    )


async def test_edit_sensor_field_updates_persist(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(
        hass, hass_storage, {"0": make_sensor_config(name="Orig", enabled=False)}
    )
    result = await _open_edit_sensor(hass, entry, 0)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "entity_id": "sensor.new_entity",
            "name": "Renamed",
            "purpose": "Return",
            "scale": "C",
            "enabled": False,
        },
    )
    assert result["type"] == FlowResultType.MENU

    config = entry.runtime_data.storage.get_sensor(0)
    assert config["name"] == "Renamed"
    assert config["entity_id"] == "sensor.new_entity"
    assert config["purpose"] == "Return"
    assert config["scale"] == "C"


async def test_edit_sensor_disable_stops_coordinator(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(
        hass,
        hass_storage,
        {"0": make_sensor_config(entity_id="sensor.temp", enabled=True)},
    )
    assert 0 in entry.runtime_data.coordinators

    result = await _open_edit_sensor(hass, entry, 0)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "entity_id": "sensor.temp",
            "name": "Test Sensor",
            "purpose": "Remote",
            "scale": "F",
            "enabled": False,
        },
    )
    assert result["type"] == FlowResultType.MENU
    assert 0 not in entry.runtime_data.coordinators

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_edit_sensor_enable_starts_coordinator(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(
        hass,
        hass_storage,
        {"0": make_sensor_config(entity_id="sensor.temp", enabled=False)},
    )
    assert 0 not in entry.runtime_data.coordinators

    result = await _open_edit_sensor(hass, entry, 0)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "entity_id": "sensor.temp",
            "name": "Test Sensor",
            "purpose": "Remote",
            "scale": "F",
            "enabled": True,
        },
    )
    assert result["type"] == FlowResultType.MENU
    assert 0 in entry.runtime_data.coordinators

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_edit_sensor_purpose_change_restarts_coordinator(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(
        hass,
        hass_storage,
        {
            "0": make_sensor_config(
                entity_id="sensor.temp", purpose="Remote", enabled=True
            )
        },
    )
    old_coordinator = entry.runtime_data.coordinators[0]

    result = await _open_edit_sensor(hass, entry, 0)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "entity_id": "sensor.temp",
            "name": "Test Sensor",
            "purpose": "Outdoor",
            "scale": "F",
            "enabled": True,
        },
    )
    assert result["type"] == FlowResultType.MENU
    # Restarted: a fresh coordinator object replaced the old one.
    assert entry.runtime_data.coordinators[0] is not old_coordinator

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_edit_sensor_rename_conflict(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(
        hass,
        hass_storage,
        {
            "0": make_sensor_config(name="Alpha", enabled=False),
            "1": make_sensor_config(name="Beta", entity_id="sensor.b", enabled=False),
        },
    )
    result = await _open_edit_sensor(hass, entry, 1)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "entity_id": "sensor.b",
            "name": "Alpha",  # collides with sensor 0
            "purpose": "Remote",
            "scale": "F",
            "enabled": False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"name": "name_duplicate"}


# --------------------------------------------------------------------------- #
# Options flow — delete
# --------------------------------------------------------------------------- #
async def _open_confirm_delete(
    hass: HomeAssistant, entry: MockConfigEntry, sensor_id: int
):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "select_sensor_to_delete"}
    )
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"sensor_id": str(sensor_id)}
    )


async def test_delete_sensor_not_confirmed(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(
        hass, hass_storage, {"0": make_sensor_config(enabled=False)}
    )
    result = await _open_confirm_delete(hass, entry, 0)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": False}
    )
    assert result["type"] == FlowResultType.MENU
    assert entry.runtime_data.storage.get_sensor(0) is not None


async def test_delete_sensor_confirmed(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    entry = await _setup_entry(
        hass,
        hass_storage,
        {"0": make_sensor_config(entity_id="sensor.temp", enabled=True)},
    )
    assert 0 in entry.runtime_data.coordinators

    result = await _open_confirm_delete(hass, entry, 0)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] == FlowResultType.MENU
    assert entry.runtime_data.storage.get_sensor(0) is None
    assert 0 not in entry.runtime_data.coordinators

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# --------------------------------------------------------------------------- #
# Options flow — done -> pair_all_sensors
# --------------------------------------------------------------------------- #
async def _run_done(hass: HomeAssistant, entry: MockConfigEntry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "done"}
    )


async def test_pair_all_sensors_complete(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    _, cf_bc = patched_broadcasts
    hass.states.async_set("sensor.a", "72.0")
    hass.states.async_set("sensor.b", "68.0")
    hass.states.async_set("sensor.c", "70.0")
    sensors = {
        "0": make_sensor_config(entity_id="sensor.a", name="A", sequence=500),
        "1": make_sensor_config(entity_id="sensor.b", name="B", sequence=42),
        "2": make_sensor_config(entity_id="sensor.c", name="C", enabled=False),
    }
    entry = await _setup_entry(hass, hass_storage, sensors)
    cf_bc.reset_mock()

    result = await _run_done(hass, entry)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "pairing_complete"

    # One pairing packet per enabled sensor; disabled sensor skipped.
    assert cf_bc.call_count == 2
    for call in cf_bc.call_args_list:
        message = SensorMessage.FromString(call.args[0])
        assert message.Command == SensorMessage.SENSORPAIR

    storage = entry.runtime_data.storage
    assert storage.get_sensor(0)["sequence"] == 1
    assert storage.get_sensor(1)["sequence"] == 1

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_pair_all_sensors_partial(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    _, cf_bc = patched_broadcasts
    hass.states.async_set("sensor.a", "72.0")
    hass.states.async_set("sensor.b", "unavailable")
    sensors = {
        "0": make_sensor_config(entity_id="sensor.a", name="Alpha"),
        "1": make_sensor_config(entity_id="sensor.b", name="Beta"),
    }
    entry = await _setup_entry(hass, hass_storage, sensors)
    cf_bc.reset_mock()

    result = await _run_done(hass, entry)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "pairing_partial"
    assert "Beta" in result["description_placeholders"]["failed"]
    assert cf_bc.call_count == 1

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_pair_all_sensors_failed(
    hass: HomeAssistant, hass_storage: dict, patched_broadcasts
) -> None:
    _, cf_bc = patched_broadcasts
    hass.states.async_set("sensor.a", "unavailable")
    hass.states.async_set("sensor.b", "unavailable")
    sensors = {
        "0": make_sensor_config(entity_id="sensor.a", name="Alpha"),
        "1": make_sensor_config(entity_id="sensor.b", name="Beta"),
    }
    entry = await _setup_entry(hass, hass_storage, sensors)
    cf_bc.reset_mock()

    result = await _run_done(hass, entry)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "pairing_failed"
    assert cf_bc.call_count == 0

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
