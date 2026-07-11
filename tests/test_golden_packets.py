"""Byte-for-byte protocol verification against the C# golden packets.

This is the core protocol guarantee of the repo: packets built by this Python
implementation must be byte-identical to the ones the verified C# app produced.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.venstar_acc_tsenwifi_emulator.protobuf.sensor_message_pb2 import (
    INFO,
    SensorMessage,
)
from custom_components.venstar_acc_tsenwifi_emulator.venstar_sensor import VenstarSensor

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "csharp_golden_packets.json"
_FIXTURE = json.loads(_FIXTURE_PATH.read_text())
_MAC_PREFIX = _FIXTURE["_meta"]["mac_prefix"]
_PACKETS = _FIXTURE["packets"]

# Purpose string -> protobuf INFO.SensorType enum value.
_TYPE_BY_PURPOSE = {
    "Outdoor": INFO.OUTDOOR,
    "Remote": INFO.REMOTE,
    "Return": INFO.RETURN,
    "Supply": INFO.SUPPLY,
}

_IDS = [f"{p['command']}:{p['expected']['name']}" for p in _PACKETS]


def _make_sensor(packet: dict) -> VenstarSensor:
    """Build a VenstarSensor from a fixture packet's expected + source fields."""
    exp = packet["expected"]
    return VenstarSensor(
        sensor_id=exp["sensor_id"],
        mac_prefix=_MAC_PREFIX,
        name=exp["name"],
        purpose=exp["purpose"],
        scale=packet["source"]["scale"],
        sequence=exp["sequence"],
    )


@pytest.mark.parametrize("packet", _PACKETS, ids=_IDS)
def test_packet_hex_matches_golden(packet: dict) -> None:
    """Building the packet reproduces the exact C# wire bytes."""
    sensor = _make_sensor(packet)
    temperature = packet["source"]["input_temperature"]

    if packet["command"] == "SENSORPAIR":
        built = sensor.build_pairing_packet(temperature)
    else:
        built = sensor.build_data_packet(temperature)

    assert built.hex() == packet["hex"]


@pytest.mark.parametrize("packet", _PACKETS, ids=_IDS)
def test_packet_decodes_to_expected_fields(packet: dict) -> None:
    """Decoding the golden hex recovers every expected field.

    This validates the checked-in sensor_message_pb2 against protobuf-net output.
    """
    exp = packet["expected"]
    message = SensorMessage.FromString(bytes.fromhex(packet["hex"]))

    expected_command = (
        SensorMessage.SENSORPAIR
        if packet["command"] == "SENSORPAIR"
        else SensorMessage.SENSORDATA
    )
    assert message.Command == expected_command

    info = message.SensorData.Info
    assert info.Mac == exp["mac"]
    assert info.SensorId == exp["sensor_id"]
    assert info.Name == exp["name"]
    assert info.Sequence == exp["sequence"]
    assert info.Type == _TYPE_BY_PURPOSE[exp["purpose"]]
    assert info.Battery == exp["battery"] == 100
    assert info.FwMajor == exp["fw_major"] == 4
    assert info.FwMinor == exp["fw_minor"] == 2
    assert info.Power == INFO.BATTERY
    assert info.Model == INFO.TEMPSENSOR

    if exp["temperature_field_present"]:
        assert info.HasField("Temperature")
        assert info.Temperature == exp["temp_index"]
    else:
        # index 0 must be ABSENT on the wire, not present-and-zero.
        assert not info.HasField("Temperature")
        # Decoders still read an absent optional uint32 as 0.
        assert info.Temperature == 0 == exp["temp_index"]

    # Humidity is never sent by the C# app.
    assert not info.HasField("Humidity")


def test_index_zero_packet_omits_temperature_field() -> None:
    """The -40.0C (index 0) fixture is the one with Temperature absent."""
    zero_packets = [
        p for p in _PACKETS if not p["expected"]["temperature_field_present"]
    ]
    assert zero_packets, "fixture should contain an index-0 packet"
    for packet in zero_packets:
        info = SensorMessage.FromString(bytes.fromhex(packet["hex"])).SensorData.Info
        assert not info.HasField("Temperature")
