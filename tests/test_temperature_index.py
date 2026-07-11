"""Unit tests for get_temperature_index() — pure, no HA fixtures needed."""
from __future__ import annotations

import pytest

from custom_components.venstar_acc_tsenwifi_emulator.venstar_sensor import (
    get_temperature_index,
)


@pytest.mark.parametrize(
    ("temperature", "expected"),
    [
        # Anchors
        pytest.param(-40.0, 0, id="C:-40.0->0 (min)"),
        pytest.param(86.5, 253, id="C:86.5->253 (max)"),
        pytest.param(0.0, 80, id="C:0.0->80"),
        pytest.param(22.0, 124, id="C:22.0->124"),
        pytest.param(-10.5, 59, id="C:-10.5->59"),
        # Half-degree rounding is ROUND_HALF_UP
        pytest.param(22.25, 125, id="C:22.25->22.5->125"),
        pytest.param(22.2, 124, id="C:22.2->22.0->124"),
        pytest.param(22.75, 126, id="C:22.75->23.0->126"),
        # Bounds via rounding (valid because they round in-range)
        pytest.param(86.7, 253, id="C:86.7->rounds-86.5->253"),
        pytest.param(-40.2, 0, id="C:-40.2->rounds--40.0->0"),
        # Float-representation edge cases (implementation goes via Decimal(str(x)))
        pytest.param(0.25, 81, id="C:0.25->0.5->81"),
        pytest.param(-0.25, 79, id="C:-0.25->-0.5->79"),
        pytest.param(20.049999999999997, 120, id="C:20.04999...->20.0->120"),
    ],
)
def test_celsius_index(temperature: float, expected: int) -> None:
    """Celsius temperatures map to the expected byte index."""
    assert get_temperature_index(temperature, "C") == expected


@pytest.mark.parametrize(
    ("temperature", "expected"),
    [
        # Anchors
        pytest.param(72.0, 124, id="F:72.0->22.0C->124"),
        pytest.param(68.0, 120, id="F:68.0->20.0C->120"),
        pytest.param(-40.0, 0, id="F:-40.0->0 (min)"),
        pytest.param(188.0, 253, id="F:188.0->253 (max)"),
        # Whole-degree rounding half-up on positives
        pytest.param(72.5, 126, id="F:72.5->73F->126"),
        pytest.param(72.4, 124, id="F:72.4->72F->124"),
        # Negative rule (abs, round half-up, negate)
        pytest.param(-35.5, 4, id="F:-35.5->-36F->4 (away from zero)"),
        pytest.param(-0.5, 43, id="F:-0.5->-1F->43"),
        # In-range because it rounds down to 188
        pytest.param(188.4, 253, id="F:188.4->188F->253"),
    ],
)
def test_fahrenheit_index(temperature: float, expected: int) -> None:
    """Fahrenheit temperatures map to the expected byte index."""
    assert get_temperature_index(temperature, "F") == expected


@pytest.mark.parametrize(
    ("temperature", "scale"),
    [
        pytest.param(86.8, "C", id="C:86.8->rounds-87.0 out of range"),
        pytest.param(-40.3, "C", id="C:-40.3 out of range"),
        pytest.param(-40.6, "F", id="F:-40.6->-41F out of range"),
        pytest.param(188.6, "F", id="F:188.6->189F out of range"),
    ],
)
def test_out_of_range_raises(temperature: float, scale: str) -> None:
    """Temperatures that round outside the valid band raise ValueError."""
    with pytest.raises(ValueError):
        get_temperature_index(temperature, scale)


@pytest.mark.parametrize("scale", ["K", "", "f", "c"])
def test_invalid_scale_raises(scale: str) -> None:
    """Any scale other than exactly 'F' or 'C' raises ValueError."""
    with pytest.raises(ValueError):
        get_temperature_index(20.0, scale)
