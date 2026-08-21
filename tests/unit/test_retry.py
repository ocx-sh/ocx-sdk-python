# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Contract tests for `ocx_sdk._retry` (C-004, S-004).

Named rows from the design's mechanism matrix: `test_backoff_sequence` and
`test_retry_after_ceiling`. Every wait runs through an injected seam — a real
`time.sleep` in a unit test is a defect, so the drivers are exercised with
recorders that return instantly.

S-004 lives here too: a registry 429 arrives as exit 75 and is retried per
policy, with `attempts` recorded on the failure the caller finally sees.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from ocx_sdk._errors import ExitCode, OcxProcessError, TempFailError
from ocx_sdk._retry import _delays, _resolve_delay, run_with_retry, run_with_retry_async
from ocx_sdk._types import RetryPolicy


def _always(_err: Exception) -> bool:
    return True


def _never(_err: Exception) -> bool:
    return False


def _raise_temp_fail() -> str:
    raise TempFailError(ExitCode.TEMP_FAIL, ["ocx", "package", "pull", "app"], "429 Too Many Requests")


async def _raise_temp_fail_async() -> str:
    return _raise_temp_fail()


def _recording_uniform(calls: list[tuple[float, float]]) -> Callable[[float, float], float]:
    """Return a `random.uniform` stand-in that records its bounds and draws the top."""

    def draw(low: float, high: float) -> float:
        calls.append((low, high))
        return high

    return draw


def _async_recorder(slept: list[float]) -> Callable[[float], Awaitable[None]]:
    """Return an `asyncio.sleep` stand-in that records the delay and returns at once."""

    async def sleep(delay: float) -> None:
        slept.append(delay)

    return sleep


def test_backoff_sequence() -> None:
    assert list(_delays(RetryPolicy(attempts=4, jitter=False))) == [1.0, 2.0, 4.0]


def test_backoff_sequence_caps_at_max_backoff() -> None:
    policy = RetryPolicy(attempts=6, jitter=False, max_backoff=5.0)

    assert list(_delays(policy)) == [1.0, 2.0, 4.0, 5.0, 5.0]


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [
        pytest.param(1, 0, id="single-attempt-never-waits"),
        pytest.param(2, 1, id="one-retry"),
        pytest.param(3, 2, id="two-retries"),
    ],
)
def test_delays_yields_one_wait_per_retry(attempts: int, expected: int) -> None:
    assert len(list(_delays(RetryPolicy(attempts=attempts, jitter=False)))) == expected


def test_full_jitter_draws_between_zero_and_the_capped_delay() -> None:
    calls: list[tuple[float, float]] = []

    delays = list(_delays(RetryPolicy(attempts=3), random_fn=_recording_uniform(calls)))

    assert calls == [(0.0, 1.0), (0.0, 2.0)]
    assert delays == [1.0, 2.0]


def test_jitter_defaults_to_the_stdlib_uniform_draw() -> None:
    delays = list(_delays(RetryPolicy(attempts=3)))

    assert len(delays) == 2
    assert all(0.0 <= delay <= 2.0 for delay in delays)


def test_backoff_survives_an_attempt_count_that_would_overflow_a_float() -> None:
    delays = list(_delays(RetryPolicy(attempts=1200, jitter=False)))

    assert len(delays) == 1199
    assert delays[-1] == 30.0
    assert max(delays) == 30.0


def test_backoff_starts_capped_when_the_first_delay_already_exceeds_the_ceiling() -> None:
    policy = RetryPolicy(attempts=3, jitter=False, backoff=100.0, max_backoff=30.0)

    assert list(_delays(policy)) == [30.0, 30.0]


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        pytest.param(None, 4.0, id="no-server-value-uses-backoff"),
        pytest.param(120.0, 120.0, id="above-max-backoff-is-not-truncated"),
        pytest.param(300.0, 300.0, id="exactly-at-the-ceiling"),
        pytest.param(300.5, None, id="beyond-the-ceiling-gives-up"),
    ],
)
def test_retry_after_ceiling(retry_after: float | None, expected: float | None) -> None:
    assert _resolve_delay(RetryPolicy(), 4.0, retry_after) == expected


def test_run_with_retry_returns_the_first_success() -> None:
    calls: list[int] = []
    slept: list[float] = []

    def once() -> str:
        calls.append(1)
        return "ok"

    assert run_with_retry(once, RetryPolicy(jitter=False), _always, sleep=slept.append) == "ok"
    assert (calls, slept) == ([1], [])


def test_run_with_retry_retries_until_it_succeeds() -> None:
    slept: list[float] = []
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            return _raise_temp_fail()
        return "ok"

    result = run_with_retry(flaky, RetryPolicy(jitter=False), _always, sleep=slept.append)

    assert result == "ok"
    assert slept == [1.0, 2.0]


def test_run_with_retry_reraises_at_once_when_classify_rejects() -> None:
    slept: list[float] = []

    with pytest.raises(TempFailError, match="exited 75"):
        run_with_retry(_raise_temp_fail, RetryPolicy(jitter=False), _never, sleep=slept.append)

    assert slept == []


def test_run_with_retry_records_attempts_on_the_final_failure() -> None:
    slept: list[float] = []

    with pytest.raises(TempFailError, match="after 3 attempts") as caught:
        run_with_retry(_raise_temp_fail, RetryPolicy(jitter=False), _always, sleep=slept.append)

    assert caught.value.attempts == 3
    assert slept == [1.0, 2.0]


def test_run_with_retry_leaves_non_process_errors_untouched() -> None:
    slept: list[float] = []

    def boom() -> str:
        raise RuntimeError("connection reset")

    with pytest.raises(RuntimeError, match="connection reset"):
        run_with_retry(boom, RetryPolicy(attempts=2, jitter=False), _always, sleep=slept.append)

    assert slept == [1.0]


def test_run_with_retry_falls_back_to_the_policy_sleep() -> None:
    slept: list[float] = []
    policy = RetryPolicy(attempts=2, jitter=False, sleep=slept.append)

    with pytest.raises(TempFailError, match="exited 75"):
        run_with_retry(_raise_temp_fail, policy, _always)

    assert slept == [1.0]


def test_run_with_retry_falls_back_to_time_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("ocx_sdk._retry.time.sleep", slept.append)

    with pytest.raises(TempFailError, match="exited 75"):
        run_with_retry(_raise_temp_fail, RetryPolicy(attempts=2, jitter=False), _always)

    assert slept == [1.0]


def test_run_with_retry_honors_a_server_retry_after() -> None:
    slept: list[float] = []

    with pytest.raises(TempFailError, match="exited 75"):
        run_with_retry(
            _raise_temp_fail,
            RetryPolicy(attempts=2, jitter=False),
            _always,
            sleep=slept.append,
            retry_after=lambda _err: 45.0,
        )

    assert slept == [45.0]


def test_run_with_retry_threads_jitter_through_to_the_delay_generator() -> None:
    slept: list[float] = []
    draws: list[tuple[float, float]] = []

    with pytest.raises(TempFailError, match="after 3 attempts"):
        run_with_retry(
            _raise_temp_fail,
            RetryPolicy(),
            _always,
            sleep=slept.append,
            random_fn=_recording_uniform(draws),
        )

    assert draws == [(0.0, 1.0), (0.0, 2.0)]
    assert slept == [1.0, 2.0]


def test_run_with_retry_gives_up_when_retry_after_exceeds_the_ceiling() -> None:
    slept: list[float] = []

    with pytest.raises(TempFailError, match="exited 75") as caught:
        run_with_retry(
            _raise_temp_fail,
            RetryPolicy(jitter=False),
            _always,
            sleep=slept.append,
            retry_after=lambda _err: 3600.0,
        )

    assert slept == []
    assert caught.value.attempts == 1
    assert any("retry ceiling exceeded" in note for note in caught.value.__notes__)


async def test_run_with_retry_async_returns_the_first_success() -> None:
    slept: list[float] = []

    async def once() -> str:
        return "ok"

    assert await run_with_retry_async(once, RetryPolicy(), _always, sleep=_async_recorder(slept)) == "ok"
    assert slept == []


async def test_run_with_retry_async_retries_until_it_succeeds() -> None:
    slept: list[float] = []
    calls: list[int] = []

    async def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            return _raise_temp_fail()
        return "ok"

    result = await run_with_retry_async(flaky, RetryPolicy(jitter=False), _always, sleep=_async_recorder(slept))

    assert result == "ok"
    assert slept == [1.0, 2.0]


async def test_run_with_retry_async_records_attempts_on_the_final_failure() -> None:
    slept: list[float] = []

    with pytest.raises(TempFailError, match="after 3 attempts") as caught:
        await run_with_retry_async(
            _raise_temp_fail_async, RetryPolicy(jitter=False), _always, sleep=_async_recorder(slept)
        )

    assert caught.value.attempts == 3
    assert slept == [1.0, 2.0]


async def test_run_with_retry_async_reraises_at_once_when_classify_rejects() -> None:
    slept: list[float] = []

    with pytest.raises(TempFailError, match="exited 75"):
        await run_with_retry_async(
            _raise_temp_fail_async, RetryPolicy(jitter=False), _never, sleep=_async_recorder(slept)
        )

    assert slept == []


async def test_run_with_retry_async_gives_up_when_retry_after_exceeds_the_ceiling() -> None:
    slept: list[float] = []

    with pytest.raises(TempFailError, match="exited 75"):
        await run_with_retry_async(
            _raise_temp_fail_async,
            RetryPolicy(jitter=False),
            _always,
            sleep=_async_recorder(slept),
            retry_after=lambda _err: 3600.0,
        )

    assert slept == []


async def test_run_with_retry_async_honors_a_server_retry_after_above_max_backoff() -> None:
    slept: list[float] = []

    with pytest.raises(TempFailError, match="exited 75"):
        await run_with_retry_async(
            _raise_temp_fail_async,
            RetryPolicy(attempts=2, jitter=False),
            _always,
            sleep=_async_recorder(slept),
            retry_after=lambda _err: 120.0,
        )

    assert slept == [120.0]


async def test_run_with_retry_async_threads_jitter_through_to_the_delay_generator() -> None:
    slept: list[float] = []
    draws: list[tuple[float, float]] = []

    with pytest.raises(TempFailError, match="after 3 attempts"):
        await run_with_retry_async(
            _raise_temp_fail_async,
            RetryPolicy(),
            _always,
            sleep=_async_recorder(slept),
            random_fn=_recording_uniform(draws),
        )

    assert draws == [(0.0, 1.0), (0.0, 2.0)]
    assert slept == [1.0, 2.0]


async def test_run_with_retry_async_leaves_non_process_errors_untouched() -> None:
    slept: list[float] = []

    async def boom() -> str:
        raise RuntimeError("connection reset")

    with pytest.raises(RuntimeError, match="connection reset"):
        await run_with_retry_async(boom, RetryPolicy(attempts=2, jitter=False), _always, sleep=_async_recorder(slept))

    assert slept == [1.0]


async def test_run_with_retry_async_falls_back_to_asyncio_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("ocx_sdk._retry.asyncio.sleep", _async_recorder(slept))

    with pytest.raises(TempFailError, match="exited 75"):
        await run_with_retry_async(_raise_temp_fail_async, RetryPolicy(attempts=2, jitter=False), _always)

    assert slept == [1.0]


async def test_run_with_retry_async_ignores_the_synchronous_policy_sleep() -> None:
    policy_slept: list[float] = []
    slept: list[float] = []
    policy = RetryPolicy(attempts=2, jitter=False, sleep=policy_slept.append)

    with pytest.raises(TempFailError, match="exited 75"):
        await run_with_retry_async(_raise_temp_fail_async, policy, _always, sleep=_async_recorder(slept))

    assert policy_slept == []
    assert slept == [1.0]


def test_retry_driver_reports_attempts_through_the_process_error_contract() -> None:
    slept: list[float] = []

    with pytest.raises(OcxProcessError, match="exited 75") as caught:
        run_with_retry(_raise_temp_fail, RetryPolicy(attempts=2, jitter=False), _always, sleep=slept.append)

    assert caught.value.retryable is True
    assert caught.value.attempts == 2
