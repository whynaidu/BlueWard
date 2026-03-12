"""Proximity zone classification with hysteresis."""

from enum import Enum

from .config import ZoneThresholds


class ProximityZone(Enum):
    IMMEDIATE = "immediate"  # < 0.5m
    NEAR = "near"            # 0.5-2m
    FAR = "far"              # 2-5m
    OUT_OF_RANGE = "out_of_range"


HYSTERESIS_DB = 5  # dBm hysteresis to prevent zone flapping


def classify_zone(
    smoothed_rssi: float,
    thresholds: ZoneThresholds,
    current_zone: ProximityZone,
    hysteresis: int = HYSTERESIS_DB,
) -> ProximityZone:
    """Classify RSSI into proximity zone with hysteresis.

    Hysteresis prevents rapid zone switching at boundaries by requiring
    a larger RSSI change to transition away from the current zone.
    """
    h = hysteresis

    if current_zone == ProximityZone.IMMEDIATE:
        if smoothed_rssi < thresholds.immediate - h:
            if smoothed_rssi >= thresholds.near:
                return ProximityZone.NEAR
            elif smoothed_rssi >= thresholds.far:
                return ProximityZone.FAR
            else:
                return ProximityZone.OUT_OF_RANGE
    elif current_zone == ProximityZone.NEAR:
        if smoothed_rssi > thresholds.immediate + h:
            return ProximityZone.IMMEDIATE
        elif smoothed_rssi < thresholds.near - h:
            if smoothed_rssi >= thresholds.far:
                return ProximityZone.FAR
            else:
                return ProximityZone.OUT_OF_RANGE
    elif current_zone == ProximityZone.FAR:
        if smoothed_rssi > thresholds.near + h:
            if smoothed_rssi > thresholds.immediate + h:
                return ProximityZone.IMMEDIATE
            return ProximityZone.NEAR
        elif smoothed_rssi < thresholds.far - h:
            return ProximityZone.OUT_OF_RANGE
    elif current_zone == ProximityZone.OUT_OF_RANGE:
        if smoothed_rssi > thresholds.far + h:
            if smoothed_rssi > thresholds.near + h:
                if smoothed_rssi > thresholds.immediate + h:
                    return ProximityZone.IMMEDIATE
                return ProximityZone.NEAR
            return ProximityZone.FAR

    return current_zone
