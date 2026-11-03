"""The sliding-window limiter every public door of the hub shares."""

import threading

import pytest

from rebase_core.ratelimit import UNKNOWN_CLIENT, RateLimiter, client_key


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_window_admits_the_limit_then_refuses_until_the_oldest_event_leaves() -> None:
    clock = Clock()
    limiter = RateLimiter(3, 60, clock=clock)

    assert [limiter.spend("a") for _ in range(4)] == [True, True, True, False]

    clock.now += 59
    assert limiter.spend("a") is False
    clock.now += 1
    assert limiter.spend("a") is True


def test_a_refused_event_does_not_extend_the_closure() -> None:
    clock = Clock()
    limiter = RateLimiter(1, 60, clock=clock)
    assert limiter.spend("a") is True

    for _ in range(10):
        clock.now += 5
        assert limiter.spend("a") is False

    clock.now += 10  # 60 s after the one event that counted
    assert limiter.spend("a") is True


def test_keys_are_independent_and_has_room_does_not_spend() -> None:
    limiter = RateLimiter(1, 60, clock=Clock())

    assert limiter.has_room("a") is True
    assert limiter.has_room("a") is True
    assert limiter.spend("a") is True
    assert limiter.has_room("a") is False
    assert limiter.has_room("b") is True


def test_the_table_is_bounded_and_forgets_the_least_recently_seen_key() -> None:
    clock = Clock()
    limiter = RateLimiter(1, 60, max_keys=3, clock=clock)
    for key in ("a", "b", "c", "d"):
        assert limiter.spend(key) is True

    assert limiter.has_room("a") is True  # forgotten: handed a fresh window
    assert limiter.has_room("d") is False


def test_quiet_keys_are_swept_once_their_window_has_passed() -> None:
    clock = Clock()
    limiter = RateLimiter(1, 60, clock=clock)
    for index in range(50):
        limiter.spend(f"k{index}")
    clock.now += 61

    limiter.spend("fresh")

    assert list(limiter._events) == ["fresh"]


def test_concurrent_spends_never_admit_more_than_the_limit() -> None:
    limiter = RateLimiter(10, 60)
    admitted: list[bool] = []

    def hammer() -> None:
        for _ in range(20):
            admitted.append(limiter.spend("shared"))

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert admitted.count(True) == 10


def test_reset_forgets_everyone() -> None:
    limiter = RateLimiter(1, 60, clock=Clock())
    limiter.spend("a")
    limiter.reset()
    assert limiter.spend("a") is True


@pytest.mark.parametrize(
    ("address", "key"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:1:2:3:4", "2001:db8:1:2::/64"),
        ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
        ("::ffff:203.0.113.9", "203.0.113.9"),
        ("not-an-address", "not-an-address"),
        ("", UNKNOWN_CLIENT),
        ("  ", UNKNOWN_CLIENT),
    ],
)
def test_client_key_groups_an_ipv6_visitor_on_its_64_prefix(address: str, key: str) -> None:
    assert client_key(address) == key


def test_invalid_limits_are_a_programming_error() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0, 60)
