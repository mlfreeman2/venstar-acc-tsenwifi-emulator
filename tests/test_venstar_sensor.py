"""Unit tests for the VenstarSensor class (packet building, signatures, sequence)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from custom_components.venstar_acc_tsenwifi_emulator.protobuf.sensor_message_pb2 import (
    INFO,
    SensorMessage,
)
from custom_components.venstar_acc_tsenwifi_emulator.venstar_sensor import VenstarSensor

_MAC_PREFIX = "428e0486d8"

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "csharp_golden_packets.json").read_text()
)
# The last fixture entry is the pairing packet whose Signature *is* the key.
_PAIR_PACKET = next(p for p in _FIXTURE["packets"] if p["command"] == "SENSORPAIR")


def _sensor(**kwargs) -> VenstarSensor:
    base = dict(
        sensor_id=0,
        mac_prefix=_MAC_PREFIX,
        name="Test",
        purpose="Remote",
        scale="F",
        sequence=1,
    )
    base.update(kwargs)
    return VenstarSensor(**base)


@pytest.mark.parametrize(
    ("sensor_id", "prefix", "expected"),
    [
        pytest.param(0, "428e0486d8", "428e0486d800", id="id0->00"),
        pytest.param(19, "428e0486d8", "428e0486d813", id="id19->13(hex)"),
        pytest.param(0, "428E0486D8", "428e0486d800", id="uppercase-prefix-lowercased"),
    ],
)
def test_mac_address(sensor_id: int, prefix: str, expected: str) -> None:
    assert _sensor(sensor_id=sensor_id, mac_prefix=prefix).mac_address == expected


def test_signature_key_is_base64_sha256_of_mac() -> None:
    sensor = _sensor()
    expected = base64.b64encode(
        hashlib.sha256(sensor.mac_address.encode("utf-8")).digest()
    ).decode("utf-8")
    assert sensor.signature_key == expected


def test_signature_key_matches_fixture_pairing_signature() -> None:
    """The pairing packet's Signature field is exactly the signature key."""
    exp = _PAIR_PACKET["expected"]
    sensor = _sensor(
        sensor_id=exp["sensor_id"],
        name=exp["name"],
        purpose=exp["purpose"],
        scale=_PAIR_PACKET["source"]["scale"],
    )
    message = SensorMessage.FromString(bytes.fromhex(_PAIR_PACKET["hex"]))
    assert message.SensorData.Signature == sensor.signature_key


def test_generate_signature_roundtrip() -> None:
    """generate_signature is an HMAC-SHA256 verifiable with the derived key."""
    sensor = _sensor()
    payload = b"some info bytes"
    signature = sensor.generate_signature(payload)

    key = base64.b64decode(sensor.signature_key)
    expected = base64.b64encode(
        hmac.new(key, payload, hashlib.sha256).digest()
    ).decode("utf-8")
    assert hmac.compare_digest(signature, expected)


@pytest.mark.parametrize(
    ("purpose", "expected_type"),
    [
        pytest.param("Outdoor", INFO.OUTDOOR, id="Outdoor"),
        pytest.param("Remote", INFO.REMOTE, id="Remote"),
        pytest.param("Return", INFO.RETURN, id="Return"),
        pytest.param("Supply", INFO.SUPPLY, id="Supply"),
    ],
)
def test_build_data_packet_info_fields(purpose: str, expected_type: int) -> None:
    """Every INFO field is set correctly for each purpose."""
    sensor = _sensor(sensor_id=7, purpose=purpose, scale="C", sequence=10)
    packet = sensor.build_data_packet(22.0)

    info = SensorMessage.FromString(packet).SensorData.Info
    assert info.SensorId == 7
    assert info.Mac == "428e0486d807"
    assert info.Name == "Test"
    assert info.Sequence == 10
    assert info.Type == expected_type
    assert info.Temperature == 124
    assert info.Battery == 100
    assert info.FwMajor == 4
    assert info.FwMinor == 2
    assert info.Power == INFO.BATTERY
    assert info.Model == INFO.TEMPSENSOR


def test_build_data_packet_signature_verifies() -> None:
    """The SENSORDATA Signature is HMAC-SHA256 of the serialized INFO."""
    sensor = _sensor(scale="C", sequence=3)
    packet = sensor.build_data_packet(22.0)

    message = SensorMessage.FromString(packet)
    info_bytes = message.SensorData.Info.SerializeToString()
    key = base64.b64decode(sensor.signature_key)
    expected = base64.b64encode(
        hmac.new(key, info_bytes, hashlib.sha256).digest()
    ).decode("utf-8")
    assert hmac.compare_digest(message.SensorData.Signature, expected)


def test_build_data_packet_uses_current_then_increments_sequence() -> None:
    sensor = _sensor(scale="C", sequence=5)
    packet = sensor.build_data_packet(22.0)
    assert SensorMessage.FromString(packet).SensorData.Info.Sequence == 5
    assert sensor.sequence == 6


def test_build_data_packet_sequence_rollover() -> None:
    """Sequence 64999 rolls to 1 (the >= 65000 reset)."""
    sensor = _sensor(scale="C", sequence=64999)
    packet = sensor.build_data_packet(22.0)
    assert SensorMessage.FromString(packet).SensorData.Info.Sequence == 64999
    assert sensor.sequence == 1


def test_build_pairing_packet() -> None:
    """Pairing forces Sequence=1, uses the key as Signature, resets self.sequence."""
    sensor = _sensor(scale="C", sequence=12345)
    packet = sensor.build_pairing_packet(22.0)

    message = SensorMessage.FromString(packet)
    assert message.Command == SensorMessage.SENSORPAIR
    assert message.SensorData.Info.Sequence == 1
    assert message.SensorData.Signature == sensor.signature_key
    assert sensor.sequence == 1


def test_invalid_purpose_raises_on_build() -> None:
    sensor = _sensor(purpose="Bogus", scale="C")
    with pytest.raises(ValueError):
        sensor.build_data_packet(22.0)


def test_name_exactly_max_length_serializes() -> None:
    """A 14-char name serializes fine (enforcement lives in the flows)."""
    name = "X" * 14
    sensor = _sensor(name=name, scale="C")
    packet = sensor.build_data_packet(22.0)
    assert SensorMessage.FromString(packet).SensorData.Info.Name == name
