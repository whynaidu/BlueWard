"""Tests for blueward.filter — KalmanFilter, EMAFilter, and create_filter."""

import pytest

from blueward.filter import KalmanFilter, EMAFilter, create_filter


# ---------------------------------------------------------------------------
# KalmanFilter
# ---------------------------------------------------------------------------

class TestKalmanFilter:
    def test_first_update_returns_measurement(self):
        kf = KalmanFilter()
        assert kf.update(-60.0) == -60.0

    def test_subsequent_updates_smooth(self):
        kf = KalmanFilter()
        kf.update(-60.0)
        # A large jump should be smoothed — result stays closer to previous
        result = kf.update(-80.0)
        assert -80.0 < result < -60.0

    def test_converges_to_constant_signal(self):
        kf = KalmanFilter()
        for _ in range(100):
            val = kf.update(-55.0)
        assert abs(val - (-55.0)) < 0.1

    def test_reset_clears_state(self):
        kf = KalmanFilter()
        kf.update(-60.0)
        kf.update(-62.0)
        kf.reset()
        assert kf.x is None
        assert kf.p == 1.0
        # After reset, next update should return the raw measurement
        assert kf.update(-70.0) == -70.0

    def test_custom_noise_parameters(self):
        kf = KalmanFilter(process_noise=1.0, measurement_noise=1.0)
        kf.update(-60.0)
        # With equal noise, Kalman gain is higher so it trusts measurements more
        result = kf.update(-80.0)
        # Should track closer to -80 than with default (low process noise)
        assert result < -65.0

    def test_monotonic_tracking_of_rising_signal(self):
        """If signal steadily increases, filtered output should also increase."""
        kf = KalmanFilter()
        values = []
        for rssi in range(-80, -40, 2):
            values.append(kf.update(float(rssi)))
        # Check that the sequence is strictly increasing
        for i in range(1, len(values)):
            assert values[i] > values[i - 1]


# ---------------------------------------------------------------------------
# EMAFilter
# ---------------------------------------------------------------------------

class TestEMAFilter:
    def test_first_update_returns_measurement(self):
        ema = EMAFilter(alpha=0.3)
        assert ema.update(-60.0) == -60.0

    def test_ema_formula(self):
        ema = EMAFilter(alpha=0.5)
        ema.update(-60.0)
        result = ema.update(-80.0)
        # EMA: 0.5 * (-80) + 0.5 * (-60) = -70
        assert result == pytest.approx(-70.0)

    def test_alpha_one_tracks_immediately(self):
        ema = EMAFilter(alpha=1.0)
        ema.update(-60.0)
        assert ema.update(-80.0) == -80.0

    def test_alpha_zero_ignores_new(self):
        ema = EMAFilter(alpha=0.0)
        ema.update(-60.0)
        assert ema.update(-80.0) == -60.0

    def test_converges_to_constant(self):
        ema = EMAFilter(alpha=0.3)
        for _ in range(200):
            val = ema.update(-55.0)
        assert abs(val - (-55.0)) < 0.01

    def test_reset_clears_state(self):
        ema = EMAFilter(alpha=0.3)
        ema.update(-60.0)
        ema.reset()
        assert ema.value is None
        assert ema.update(-70.0) == -70.0


# ---------------------------------------------------------------------------
# create_filter factory
# ---------------------------------------------------------------------------

class TestCreateFilter:
    def test_default_creates_kalman(self):
        f = create_filter()
        assert isinstance(f, KalmanFilter)

    def test_kalman_explicit(self):
        f = create_filter("kalman", process_noise=0.1, measurement_noise=2.0)
        assert isinstance(f, KalmanFilter)
        assert f.q == 0.1
        assert f.r == 2.0

    def test_ema_explicit(self):
        f = create_filter("ema", ema_alpha=0.5)
        assert isinstance(f, EMAFilter)
        assert f.alpha == 0.5

    def test_unknown_method_defaults_to_kalman(self):
        f = create_filter("unknown_method")
        assert isinstance(f, KalmanFilter)
