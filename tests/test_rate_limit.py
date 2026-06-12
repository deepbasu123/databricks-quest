"""Attempt rate limiting (gap P1-18)."""

from rate_limit import SlidingWindowLimiter


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_allows_up_to_limit_then_blocks():
    clock = _Clock()
    lim = SlidingWindowLimiter(max_events=3, window_seconds=10, clock=clock)
    assert lim.allow("k") is True
    assert lim.allow("k") is True
    assert lim.allow("k") is True
    assert lim.allow("k") is False  # 4th within the window


def test_window_slides_and_reallows():
    clock = _Clock()
    lim = SlidingWindowLimiter(max_events=2, window_seconds=10, clock=clock)
    assert lim.allow("k") is True
    assert lim.allow("k") is True
    assert lim.allow("k") is False
    clock.advance(11)  # both events expire
    assert lim.allow("k") is True


def test_keys_are_independent():
    clock = _Clock()
    lim = SlidingWindowLimiter(max_events=1, window_seconds=10, clock=clock)
    assert lim.allow("a") is True
    assert lim.allow("b") is True  # different key, own budget
    assert lim.allow("a") is False


def test_zero_disables_limiting():
    lim = SlidingWindowLimiter(max_events=0, window_seconds=10)
    for _ in range(100):
        assert lim.allow("k") is True


def test_retry_after_decreases():
    clock = _Clock()
    lim = SlidingWindowLimiter(max_events=1, window_seconds=10, clock=clock)
    lim.allow("k")
    assert lim.retry_after("k") == 10.0
    clock.advance(4)
    assert lim.retry_after("k") == 6.0
    assert lim.retry_after("never-seen") == 0.0
