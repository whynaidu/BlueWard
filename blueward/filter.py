class KalmanFilter:
    """1D Kalman filter for smoothing RSSI readings."""

    def __init__(self, process_noise: float = 0.008, measurement_noise: float = 4.0):
        self.q = process_noise
        self.r = measurement_noise
        self.x: float | None = None  # estimated RSSI
        self.p: float = 1.0          # estimation error covariance

    def update(self, measurement: float) -> float:
        if self.x is None:
            self.x = measurement
            self.p = 1.0
            return self.x

        # Predict
        self.p += self.q

        # Update
        k = self.p / (self.p + self.r)  # Kalman gain
        self.x += k * (measurement - self.x)
        self.p *= (1 - k)

        return self.x

    def reset(self):
        self.x = None
        self.p = 1.0


class EMAFilter:
    """Exponential Moving Average filter for RSSI smoothing."""

    def __init__(self, alpha: float = 0.3):
        self.alpha = alpha
        self.value: float | None = None

    def update(self, measurement: float) -> float:
        if self.value is None:
            self.value = measurement
        else:
            self.value = self.alpha * measurement + (1 - self.alpha) * self.value
        return self.value

    def reset(self):
        self.value = None


def create_filter(method: str = "kalman", **kwargs):
    if method == "ema":
        return EMAFilter(alpha=kwargs.get("ema_alpha", 0.3))
    return KalmanFilter(
        process_noise=kwargs.get("process_noise", 0.008),
        measurement_noise=kwargs.get("measurement_noise", 4.0),
    )
