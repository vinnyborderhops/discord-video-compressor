import math

import pytest

from compressor.bitrate import calculate_bitrate
from compressor.errors import ValidationError


def test_prototype_formula_with_audio():
    budget = calculate_bitrate(60.0, 10.0, True)

    assert budget.total_kbps == pytest.approx(10 * 8 * 1024 * 0.97 / 60)
    assert budget.audio_kbps == 96.0
    assert budget.video_kbps == pytest.approx(budget.total_kbps - 96.0)


def test_silent_video_receives_entire_budget():
    budget = calculate_bitrate(120.0, 5.0, False)

    assert budget.audio_kbps == 0.0
    assert budget.video_kbps == budget.total_kbps


def test_small_budget_raises_when_audio_consumes_everything():
    with pytest.raises(ValidationError):
        calculate_bitrate(10_000.0, 1.0, True)


@pytest.mark.parametrize("duration", [0.0, -1.0, math.inf, math.nan])
def test_invalid_duration_is_rejected(duration):
    with pytest.raises(ValidationError):
        calculate_bitrate(duration, 10.0, False)


@pytest.mark.parametrize("target", [0.0, -1.0, math.inf, math.nan])
def test_invalid_target_is_rejected(target):
    with pytest.raises(ValidationError):
        calculate_bitrate(60.0, target, False)
