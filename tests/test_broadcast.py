"""Unit tests for broadcast_udp_packet — never sends real UDP."""
from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from custom_components.venstar_acc_tsenwifi_emulator.const import (
    BROADCAST_REPEAT_COUNT,
    UDP_PORT,
)
from custom_components.venstar_acc_tsenwifi_emulator.venstar_sensor import (
    broadcast_udp_packet,
)

_MODULE = "custom_components.venstar_acc_tsenwifi_emulator.venstar_sensor"


def _mock_socket() -> MagicMock:
    """A socket.socket mock that supports the context-manager protocol."""
    mock_socket_cls = MagicMock()
    sock = MagicMock()
    mock_socket_cls.return_value.__enter__.return_value = sock
    return mock_socket_cls, sock


def test_sets_broadcast_option_and_sends_repeated() -> None:
    packet = b"\x01\x02\x03"
    mock_socket_cls, sock = _mock_socket()

    with patch(f"{_MODULE}.socket.socket", mock_socket_cls):
        broadcast_udp_packet(packet)

    sock.setsockopt.assert_called_once_with(
        socket.SOL_SOCKET, socket.SO_BROADCAST, 1
    )
    assert sock.sendto.call_count == BROADCAST_REPEAT_COUNT
    for call in sock.sendto.call_args_list:
        assert call.args == (packet, ("255.255.255.255", UDP_PORT))


def test_custom_port_respected() -> None:
    packet = b"\xaa"
    mock_socket_cls, sock = _mock_socket()

    with patch(f"{_MODULE}.socket.socket", mock_socket_cls):
        broadcast_udp_packet(packet, port=9999)

    for call in sock.sendto.call_args_list:
        assert call.args == (packet, ("255.255.255.255", 9999))


def test_sendto_exception_propagates_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    packet = b"\x00"
    mock_socket_cls, sock = _mock_socket()
    sock.sendto.side_effect = OSError("network down")

    with patch(f"{_MODULE}.socket.socket", mock_socket_cls):
        with pytest.raises(OSError, match="network down"):
            broadcast_udp_packet(packet)

    assert "Failed to broadcast UDP packet" in caplog.text
