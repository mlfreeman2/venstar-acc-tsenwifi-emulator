"""Tests for extract_temperature and the VenstarSensorCoordinator lifecycle."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant, State

from custom_components.venstar_acc_tsenwifi_emulator import coordinator as coord_module
from custom_components.venstar_acc_tsenwifi_emulator.coordinator import (
    VenstarSensorCoordinator,
    extract_temperature,
)
from custom_components.venstar_acc_tsenwifi_emulator.storage import (
    VenstarEmulatorStorage,
)

from .conftest import GOLDEN_MAC_PREFIX, make_sensor_config, seeded_storage

_BROADCAST = (
    "custom_components.venstar_acc_tsenwifi_emulator.coordinator.broadcast_udp_packet"
)


# --------------------------------------------------------------------------- #
# extract_temperature (pure)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        pytest.param(None, None, id="none-state"),
        pytest.param(State("sensor.x", "unknown"), None, id="unknown"),
        pytest.param(State("sensor.x", "unavailable"), None, id="unavailable"),
        pytest.param(State("sensor.x", "72.5"), 72.5, id="sensor-numeric"),
        pytest.param(State("sensor.x", "on"), None, id="sensor-non-numeric"),
        pytest.param(State("sensor.x", ""), None, id="sensor-empty"),
        pytest.param(
            State("climate.x", "heat", {"current_temperature": 21.5}),
            21.5,
            id="climate-attr",
        ),
        pytest.param(
            State("climate.x", "heat", {}), None, id="climate-attr-missing"
        ),
        pytest.param(
            State("climate.x", "heat", {"current_temperature": None}),
            None,
            id="climate-attr-none",
        ),
        pytest.param(
            State("climate.x", "heat", {"current_temperature": "21.5"}),
            21.5,
            id="climate-attr-string",
        ),
    ],
)
def test_extract_temperature(state: State | None, expected: float | None) -> None:
    assert extract_temperature(state) == expected


# --------------------------------------------------------------------------- #
# Coordinator helpers
# --------------------------------------------------------------------------- #
async def _make_storage(
    hass: HomeAssistant, hass_storage: dict, sensors: dict
) -> VenstarEmulatorStorage:
    seeded_storage(hass_storage, sensors=sensors)
    storage = VenstarEmulatorStorage(hass)
    await storage.async_load(mac_prefix=GOLDEN_MAC_PREFIX)
    return storage


# --------------------------------------------------------------------------- #
# start()
# --------------------------------------------------------------------------- #
async def test_start_broadcasts_immediately(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    hass.states.async_set("sensor.temp", "72.0")
    storage = await _make_storage(
        hass, hass_storage, {"0": make_sensor_config(entity_id="sensor.temp")}
    )
    coordinator = VenstarSensorCoordinator(hass, storage, 0)

    with patch(_BROADCAST) as broadcast:
        await coordinator.start()
        assert coordinator._task is not None
        await hass.async_block_till_done()
        assert broadcast.called
        await coordinator.stop()


async def test_start_disabled_sensor_no_task(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    storage = await _make_storage(
        hass, hass_storage, {"0": make_sensor_config(enabled=False)}
    )
    coordinator = VenstarSensorCoordinator(hass, storage, 0)

    with patch(_BROADCAST):
        await coordinator.start()
    assert coordinator._task is None


async def test_start_unknown_sensor_logs_error_no_task(
    hass: HomeAssistant, hass_storage: dict, caplog: pytest.LogCaptureFixture
) -> None:
    storage = await _make_storage(hass, hass_storage, {})
    coordinator = VenstarSensorCoordinator(hass, storage, 5)

    with patch(_BROADCAST):
        await coordinator.start()
    assert coordinator._task is None
    assert "not found" in caplog.text


async def test_second_start_warns_no_duplicate_task(
    hass: HomeAssistant, hass_storage: dict, caplog: pytest.LogCaptureFixture
) -> None:
    hass.states.async_set("sensor.temp", "72.0")
    storage = await _make_storage(
        hass, hass_storage, {"0": make_sensor_config(entity_id="sensor.temp")}
    )
    coordinator = VenstarSensorCoordinator(hass, storage, 0)

    with patch(_BROADCAST):
        await coordinator.start()
        first_task = coordinator._task
        await coordinator.start()
        assert coordinator._task is first_task
        assert "already running" in caplog.text
        await coordinator.stop()


@pytest.mark.parametrize(
    ("purpose", "expected_interval"),
    [
        pytest.param("Outdoor", 300, id="outdoor-300s"),
        pytest.param("Remote", 60, id="remote-60s"),
        pytest.param("Return", 60, id="return-60s"),
    ],
)
async def test_interval_selection(
    hass: HomeAssistant,
    hass_storage: dict,
    purpose: str,
    expected_interval: int,
) -> None:
    storage = await _make_storage(
        hass, hass_storage, {"0": make_sensor_config(purpose=purpose)}
    )
    coordinator = VenstarSensorCoordinator(hass, storage, 0)

    with patch.object(
        coordinator, "_broadcast_loop", new=AsyncMock()
    ) as loop_mock:
        await coordinator.start()
        loop_mock.assert_called_once_with(expected_interval)
        await coordinator.stop()


# --------------------------------------------------------------------------- #
# stop()
# --------------------------------------------------------------------------- #
async def test_stop_idempotent_when_never_started(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    storage = await _make_storage(
        hass, hass_storage, {"0": make_sensor_config()}
    )
    coordinator = VenstarSensorCoordinator(hass, storage, 0)
    # Should not raise.
    await coordinator.stop()
    assert coordinator._task is None


async def test_stop_cancels_task(hass: HomeAssistant, hass_storage: dict) -> None:
    hass.states.async_set("sensor.temp", "72.0")
    storage = await _make_storage(
        hass, hass_storage, {"0": make_sensor_config(entity_id="sensor.temp")}
    )
    coordinator = VenstarSensorCoordinator(hass, storage, 0)

    with patch(_BROADCAST):
        await coordinator.start()
        await coordinator.stop()
    assert coordinator._task is None


# --------------------------------------------------------------------------- #
# trigger_broadcast()
# --------------------------------------------------------------------------- #
async def test_trigger_broadcast_happy_path(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    hass.states.async_set("sensor.temp", "72.0")
    storage = await _make_storage(
        hass,
        hass_storage,
        {"0": make_sensor_config(entity_id="sensor.temp", scale="F", sequence=5)},
    )
    coordinator = VenstarSensorCoordinator(hass, storage, 0)

    with patch(_BROADCAST) as broadcast, patch.object(
        storage, "async_save", wraps=storage.async_save
    ) as save_mock:
        await coordinator.trigger_broadcast()

    broadcast.assert_called_once()
    sent_packet = broadcast.call_args.args[0]

    # Sequence advanced (build increments 5 -> 6) and persisted.
    assert storage.get_sensor(0)["sequence"] == 6
    # last_packet cached and round-trips to the exact bytes broadcast.
    assert storage.get_last_packet(0) == sent_packet
    # Debounced (non-immediate) save requested.
    save_mock.assert_called_once_with(immediate=False)


@pytest.mark.parametrize(
    "state_value", ["unavailable", "not_a_number"], ids=["unavailable", "non-numeric"]
)
async def test_trigger_broadcast_unavailable(
    hass: HomeAssistant, hass_storage: dict, state_value: str
) -> None:
    hass.states.async_set("sensor.temp", state_value)
    storage = await _make_storage(
        hass,
        hass_storage,
        {"0": make_sensor_config(entity_id="sensor.temp", sequence=5)},
    )
    coordinator = VenstarSensorCoordinator(hass, storage, 0)

    with patch(_BROADCAST) as broadcast:
        await coordinator.trigger_broadcast()

    broadcast.assert_not_called()
    assert storage.get_sensor(0)["sequence"] == 5


# --------------------------------------------------------------------------- #
# Loop resilience
# --------------------------------------------------------------------------- #
async def test_broadcast_loop_survives_iteration_error(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """An exception in one iteration is caught; the loop keeps going."""
    hass.states.async_set("sensor.temp", "72.0")
    storage = await _make_storage(
        hass, hass_storage, {"0": make_sensor_config(entity_id="sensor.temp")}
    )
    coordinator = VenstarSensorCoordinator(hass, storage, 0)

    calls: list = []

    def broadcast_side_effect(packet, *args, **kwargs):
        calls.append(packet)
        if len(calls) == 1:
            raise RuntimeError("boom")
        # Stop after the loop has demonstrably continued past the error.
        hass.loop.call_soon_threadsafe(coordinator._stop_event.set)

    with patch(_BROADCAST, side_effect=broadcast_side_effect):
        # Small interval so the second iteration comes quickly.
        await asyncio.wait_for(coordinator._broadcast_loop(0.01), timeout=5)

    # Reached iteration 2 => the first-iteration exception did not kill the loop.
    assert len(calls) >= 2
