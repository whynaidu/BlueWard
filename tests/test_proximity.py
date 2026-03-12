"""Tests for blueward.proximity — zone classification with hysteresis."""

import pytest

from blueward.config import ZoneThresholds
from blueward.proximity import ProximityZone, classify_zone, HYSTERESIS_DB


# Default thresholds: immediate=-45, near=-60, far=-75
# Default hysteresis: 5 dB

@pytest.fixture
def thresholds():
    return ZoneThresholds(immediate=-45, near=-60, far=-75)


# ---------------------------------------------------------------------------
# Staying in zone (within hysteresis band) — should NOT transition
# ---------------------------------------------------------------------------

class TestNoTransition:
    """RSSI changes that stay within hysteresis should keep the current zone."""

    def test_immediate_stays_when_slightly_below(self, thresholds):
        # Threshold for leaving IMMEDIATE: rssi < immediate - h = -45 - 5 = -50
        # At -49 we are still within hysteresis band
        assert classify_zone(-49.0, thresholds, ProximityZone.IMMEDIATE) == ProximityZone.IMMEDIATE

    def test_near_stays_when_slightly_above_immediate(self, thresholds):
        # To go NEAR->IMMEDIATE needs rssi > immediate + h = -45 + 5 = -40
        # At -41 we are still NEAR
        assert classify_zone(-41.0, thresholds, ProximityZone.NEAR) == ProximityZone.NEAR

    def test_near_stays_when_slightly_below_near(self, thresholds):
        # To go NEAR->FAR needs rssi < near - h = -60 - 5 = -65
        # At -64 we are still NEAR
        assert classify_zone(-64.0, thresholds, ProximityZone.NEAR) == ProximityZone.NEAR

    def test_far_stays_when_slightly_above_near(self, thresholds):
        # To go FAR->NEAR needs rssi > near + h = -60 + 5 = -55
        # At -56 we are still FAR
        assert classify_zone(-56.0, thresholds, ProximityZone.FAR) == ProximityZone.FAR

    def test_far_stays_when_slightly_below_far(self, thresholds):
        # To go FAR->OOR needs rssi < far - h = -75 - 5 = -80
        # At -79 we are still FAR
        assert classify_zone(-79.0, thresholds, ProximityZone.FAR) == ProximityZone.FAR

    def test_oor_stays_when_slightly_above_far(self, thresholds):
        # To go OOR->FAR needs rssi > far + h = -75 + 5 = -70
        # At -71 we are still OOR
        assert classify_zone(-71.0, thresholds, ProximityZone.OUT_OF_RANGE) == ProximityZone.OUT_OF_RANGE


# ---------------------------------------------------------------------------
# Transitions from IMMEDIATE
# ---------------------------------------------------------------------------

class TestFromImmediate:
    def test_to_near(self, thresholds):
        # rssi < immediate - h = -50, but >= near = -60
        assert classify_zone(-55.0, thresholds, ProximityZone.IMMEDIATE) == ProximityZone.NEAR

    def test_to_far(self, thresholds):
        # rssi < immediate - h = -50, < near = -60, >= far = -75
        assert classify_zone(-70.0, thresholds, ProximityZone.IMMEDIATE) == ProximityZone.FAR

    def test_to_oor(self, thresholds):
        # rssi < immediate - h = -50, < near, < far
        assert classify_zone(-90.0, thresholds, ProximityZone.IMMEDIATE) == ProximityZone.OUT_OF_RANGE

    def test_boundary_exact_immediate_minus_h(self, thresholds):
        # At exactly -50, NOT less than -50, so stays IMMEDIATE
        assert classify_zone(-50.0, thresholds, ProximityZone.IMMEDIATE) == ProximityZone.IMMEDIATE

    def test_just_past_boundary(self, thresholds):
        # At -50.1, less than -50, and >= -60 -> NEAR
        assert classify_zone(-50.1, thresholds, ProximityZone.IMMEDIATE) == ProximityZone.NEAR


# ---------------------------------------------------------------------------
# Transitions from NEAR
# ---------------------------------------------------------------------------

class TestFromNear:
    def test_to_immediate(self, thresholds):
        # rssi > immediate + h = -45 + 5 = -40
        assert classify_zone(-39.0, thresholds, ProximityZone.NEAR) == ProximityZone.IMMEDIATE

    def test_to_far(self, thresholds):
        # rssi < near - h = -65, >= far = -75
        assert classify_zone(-70.0, thresholds, ProximityZone.NEAR) == ProximityZone.FAR

    def test_to_oor(self, thresholds):
        # rssi < near - h = -65, < far = -75
        assert classify_zone(-90.0, thresholds, ProximityZone.NEAR) == ProximityZone.OUT_OF_RANGE

    def test_boundary_exact_near_minus_h(self, thresholds):
        # At exactly -65, NOT less than -65, so stays NEAR
        assert classify_zone(-65.0, thresholds, ProximityZone.NEAR) == ProximityZone.NEAR


# ---------------------------------------------------------------------------
# Transitions from FAR
# ---------------------------------------------------------------------------

class TestFromFar:
    def test_to_near(self, thresholds):
        # rssi > near + h = -55, but not > immediate + h = -40
        assert classify_zone(-50.0, thresholds, ProximityZone.FAR) == ProximityZone.NEAR

    def test_to_immediate(self, thresholds):
        # rssi > immediate + h = -40
        assert classify_zone(-35.0, thresholds, ProximityZone.FAR) == ProximityZone.IMMEDIATE

    def test_to_oor(self, thresholds):
        # rssi < far - h = -80
        assert classify_zone(-85.0, thresholds, ProximityZone.FAR) == ProximityZone.OUT_OF_RANGE

    def test_boundary_exact_far_minus_h(self, thresholds):
        # At exactly -80, NOT less than -80, stays FAR
        assert classify_zone(-80.0, thresholds, ProximityZone.FAR) == ProximityZone.FAR


# ---------------------------------------------------------------------------
# Transitions from OUT_OF_RANGE
# ---------------------------------------------------------------------------

class TestFromOutOfRange:
    def test_to_far(self, thresholds):
        # rssi > far + h = -70, but not > near + h = -55
        assert classify_zone(-65.0, thresholds, ProximityZone.OUT_OF_RANGE) == ProximityZone.FAR

    def test_to_near(self, thresholds):
        # rssi > near + h = -55, but not > immediate + h = -40
        assert classify_zone(-50.0, thresholds, ProximityZone.OUT_OF_RANGE) == ProximityZone.NEAR

    def test_to_immediate(self, thresholds):
        # rssi > immediate + h = -40
        assert classify_zone(-35.0, thresholds, ProximityZone.OUT_OF_RANGE) == ProximityZone.IMMEDIATE

    def test_boundary_exact_far_plus_h(self, thresholds):
        # At exactly -70, NOT greater than -70, stays OOR
        assert classify_zone(-70.0, thresholds, ProximityZone.OUT_OF_RANGE) == ProximityZone.OUT_OF_RANGE


# ---------------------------------------------------------------------------
# Custom hysteresis
# ---------------------------------------------------------------------------

class TestCustomHysteresis:
    def test_zero_hysteresis(self, thresholds):
        """With zero hysteresis, transitions happen exactly at thresholds."""
        # NEAR with rssi just above immediate -> IMMEDIATE
        zone = classify_zone(-44.0, thresholds, ProximityZone.NEAR, hysteresis=0)
        assert zone == ProximityZone.IMMEDIATE

    def test_large_hysteresis_prevents_transition(self, thresholds):
        """Large hysteresis prevents transitions that would happen with default."""
        # From IMMEDIATE, -55 would normally go to NEAR (with h=5, boundary=-50)
        # But with h=20, boundary = -45-20=-65, so -55 > -65 => stays IMMEDIATE
        zone = classify_zone(-55.0, thresholds, ProximityZone.IMMEDIATE, hysteresis=20)
        assert zone == ProximityZone.IMMEDIATE


# ---------------------------------------------------------------------------
# Roundtrip / sequence test
# ---------------------------------------------------------------------------

class TestSequence:
    def test_walk_away_and_return(self, thresholds):
        """Simulate walking away and returning — zones should change with hysteresis lag."""
        zone = ProximityZone.IMMEDIATE

        # Walking away: gradually decreasing RSSI
        rssi_sequence = [-42, -46, -50, -52, -58, -62, -66, -70, -76, -82]
        zones_going = []
        for rssi in rssi_sequence:
            zone = classify_zone(float(rssi), thresholds, zone)
            zones_going.append(zone)

        # Should eventually reach OUT_OF_RANGE
        assert zones_going[-1] == ProximityZone.OUT_OF_RANGE

        # Walking back: gradually increasing RSSI
        rssi_return = [-78, -72, -68, -62, -56, -50, -44, -38]
        zones_return = []
        for rssi in rssi_return:
            zone = classify_zone(float(rssi), thresholds, zone)
            zones_return.append(zone)

        # Should eventually return to IMMEDIATE
        assert zones_return[-1] == ProximityZone.IMMEDIATE

    def test_never_skips_to_wrong_direction(self, thresholds):
        """RSSI can cause multi-zone jumps (e.g., IMMEDIATE -> OOR) which is valid."""
        zone = classify_zone(-90.0, thresholds, ProximityZone.IMMEDIATE)
        assert zone == ProximityZone.OUT_OF_RANGE
