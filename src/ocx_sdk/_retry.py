# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Retry driver for transient failures (contract C-004).

A pure delay generator plus two small drivers, one sync and one async. The
drivers are deliberate duplicates rather than a sans-io bridge: the bridge
would be more code than the thing it abstracts.

Callers own the taxonomy. `_process` classifies by exit code through
`RetryPolicy.retry_on`; `_dist` classifies by transport condition (connect and
read timeouts, connection resets, HTTP 429 and 5xx). Auth failures and
checksum mismatches are never retryable anywhere.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Iterator

from ._errors import OcxProcessError
from ._types import RetryPolicy


def _delays(policy: RetryPolicy, *, random_fn: Callable[[float, float], float] | None = None) -> Iterator[float]:
    """Yield the wait before each retry — `policy.attempts - 1` values.

    Exponential backoff capped at `max_backoff`, then full jitter: the capped
    value becomes the upper bound of a uniform draw rather than the delay
    itself, so concurrent clients spread out. The delay accumulates
    iteratively rather than raising `multiplier` to a power — the capped value
    is the same, and a large `attempts` cannot overflow a float on the way.

    Args:
        policy: Supplies the backoff shape and the attempt count.
        random_fn: Uniform-draw seam, defaulting to `random.uniform`. Tests
            inject a deterministic one.

    Yields:
        Seconds to wait before the next attempt.
    """
    draw = random_fn or random.uniform
    delay = min(policy.backoff, policy.max_backoff)
    for _ in range(policy.attempts - 1):
        yield draw(0.0, delay) if policy.jitter else delay
        delay = min(delay * policy.multiplier, policy.max_backoff)


def _resolve_delay(policy: RetryPolicy, computed: float, retry_after: float | None) -> float | None:
    """Reconcile the computed backoff with a server-supplied `Retry-After`.

    Args:
        policy: Supplies `max_retry_after`, the ceiling for a server value.
        computed: The delay `_delays` produced for this retry.
        retry_after: Seconds the server asked for, or `None` if it said nothing.

    Returns:
        The delay to wait, or `None` when the caller must stop retrying
        because the server asked for longer than the policy is willing to
        wait. Beyond the ceiling the answer is to fail, not to sleep on it.
    """
    if retry_after is None:
        return computed
    return retry_after if retry_after <= policy.max_retry_after else None


def run_with_retry[T](
    fn: Callable[[], T],
    policy: RetryPolicy,
    classify: Callable[[Exception], bool],
    *,
    sleep: Callable[[float], None] | None = None,
    retry_after: Callable[[Exception], float | None] | None = None,
    random_fn: Callable[[float, float], float] | None = None,
) -> T:
    """Call `fn`, retrying the failures `classify` accepts until the policy runs out.

    Args:
        fn: The operation to attempt. Called once per attempt.
        policy: Attempt count and backoff shape.
        classify: Decides whether a raised exception is worth retrying.
        sleep: Sleep seam; defaults to `policy.sleep`, then `time.sleep`.
        retry_after: Reads a server-requested delay off the exception, for
            callers whose failures carry one (HTTP 429).
        random_fn: Jitter seam, passed to `_delays`.

    Returns:
        Whatever the first successful attempt returned.

    Raises:
        Exception: The final failure, re-raised once `classify` rejects it or
            the attempts run out. `OcxProcessError.attempts` records how many
            attempts were made.
    """
    pause = sleep or policy.sleep or time.sleep
    delays = _delays(policy, random_fn=random_fn)
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as err:
            if isinstance(err, OcxProcessError):
                err.attempts = attempt
            if not classify(err):
                raise
            computed = next(delays, None)
            if computed is None:
                raise
            requested = retry_after(err) if retry_after else None
            delay = _resolve_delay(policy, computed, requested)
            if delay is None:
                err.add_note(
                    f"retry ceiling exceeded: Retry-After {requested}s > max_retry_after {policy.max_retry_after}s"
                )
                raise
            pause(delay)


async def run_with_retry_async[T](
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    classify: Callable[[Exception], bool],
    *,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    retry_after: Callable[[Exception], float | None] | None = None,
    random_fn: Callable[[float, float], float] | None = None,
) -> T:
    """Await `fn`, retrying the failures `classify` accepts until the policy runs out.

    The async twin of `run_with_retry`. `RetryPolicy.sleep` is ignored here —
    it is a synchronous callable — so the seam is the `sleep` argument, which
    defaults to `asyncio.sleep`.

    Args:
        fn: Builds the awaitable to attempt. Called once per attempt.
        policy: Attempt count and backoff shape.
        classify: Decides whether a raised exception is worth retrying.
        sleep: Async sleep seam; defaults to `asyncio.sleep`.
        retry_after: Reads a server-requested delay off the exception.
        random_fn: Jitter seam, passed to `_delays`.

    Returns:
        Whatever the first successful attempt returned.

    Raises:
        Exception: The final failure, re-raised once `classify` rejects it or
            the attempts run out.
    """
    pause = sleep or asyncio.sleep
    delays = _delays(policy, random_fn=random_fn)
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except Exception as err:
            if isinstance(err, OcxProcessError):
                err.attempts = attempt
            if not classify(err):
                raise
            computed = next(delays, None)
            if computed is None:
                raise
            requested = retry_after(err) if retry_after else None
            delay = _resolve_delay(policy, computed, requested)
            if delay is None:
                err.add_note(
                    f"retry ceiling exceeded: Retry-After {requested}s > max_retry_after {policy.max_retry_after}s"
                )
                raise
            await pause(delay)
