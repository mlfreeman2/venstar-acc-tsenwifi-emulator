"""Shared fixtures and helpers for the Venstar ACC-TSENWIFI Emulator tests."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.venstar_acc_tsenwifi_emulator.const import (
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)

# MAC prefix used by the checked-in golden packets (tests/fixtures). Using this
# everywhere keeps packet comparisons consistent across the whole suite.
GOLDEN_MAC_PREFIX = "428e0486d8"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of this custom integration in every test (mandatory)."""
    yield


@pytest.fixture
def broadcast_mocks():
    """Patch broadcast_udp_packet at both points of use (never send real UDP).

    ``__init__`` (services) and ``coordinator`` each import broadcast_udp_packet
    into their own namespace, so both must be patched. Yields
    ``(init_mock, coordinator_mock)``.
    """
    with patch(
        "custom_components.venstar_acc_tsenwifi_emulator.broadcast_udp_packet"
    ) as init_mock, patch(
        "custom_components.venstar_acc_tsenwifi_emulator.coordinator.broadcast_udp_packet"
    ) as coordinator_mock:
        yield init_mock, coordinator_mock


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A config entry using the golden-packet MAC prefix."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={"mac_prefix": GOLDEN_MAC_PREFIX},
        title="Venstar ACC-TSENWIFI Emulator",
    )


def make_sensor_config(
    entity_id: str = "sensor.test_temp",
    name: str = "Test Sensor",
    purpose: str = "Remote",
    scale: str = "F",
    enabled: bool = True,
    sequence: int = 1,
) -> dict[str, Any]:
    """Build a single sensor config dict in the storage shape."""
    return {
        "entity_id": entity_id,
        "name": name,
        "purpose": purpose,
        "scale": scale,
        "enabled": enabled,
        "sequence": sequence,
    }


def seeded_storage(
    hass_storage: dict[str, Any],
    sensors: dict[str, dict[str, Any]] | None = None,
    mac_prefix: str = GOLDEN_MAC_PREFIX,
) -> None:
    """Pre-populate the ``hass_storage`` fixture for this integration.

    Writes the integration's Store payload directly so that
    ``VenstarEmulatorStorage.async_load`` (and ``async_setup_entry``) find the
    given sensors already configured.
    """
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "mac_prefix": mac_prefix,
            "sensors": sensors or {},
        },
    }
