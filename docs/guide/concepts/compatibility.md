# Compatibility

Two independent compatibility questions, both pre-1.0: which ocx binaries
does this SDK understand, and what does *this SDK's own* version number
promise you.

## Which ocx this SDK understands: a tested window, not a promised range

ocx's own policy is that even interfaces can break pre-1.0, announced in
its changelog only — and it has: exit-code semantics moved, JSON envelopes
reshaped, the lockfile format hard-broke a major version. Given that, the
SDK does not promise compatibility with "any ocx 0.x" — it states the
window it actually tests against.

```python
from ocx_sdk import MIN_SUPPORTED, TESTED_OCX_VERSION

assert TESTED_OCX_VERSION == MIN_SUPPORTED  # true at this SDK's initial release
```

[`TESTED_OCX_VERSION`](../../reference/api.md#ocx_sdk.TESTED_OCX_VERSION) is
the exact version this SDK's contract tests run against — **not** a
selection default; [`bootstrap.ensure()`](../../reference/api.md#ocx_sdk.ensure)
defaults to the latest stable release regardless. It exists so you can pin
deliberately: `bootstrap.ensure(version=TESTED_OCX_VERSION)` if you want
exactly the binary this SDK release was built against.

[`MIN_SUPPORTED`](../../reference/api.md#ocx_sdk.MIN_SUPPORTED) is the floor.
The first typed call on an `Ocx` handle probes `ocx version` and gates on it:

- **Below `MIN_SUPPORTED`** — raises
  [`VersionCompatError`](../../reference/api.md#ocx_sdk.VersionCompatError).

  ```python
  from ocx_sdk import MIN_SUPPORTED, VersionCompatError

  error = VersionCompatError(found="0.4.0", minimum=MIN_SUPPORTED)
  assert "older than the minimum supported" in str(error)
  ```

- **Above `TESTED_OCX_VERSION`** — logs a `DEBUG`-level note on the
  `"ocx_sdk"` logger and proceeds. Never a warning: CI runs a latest-ocx
  canary alongside the pinned version, so upstream drift surfaces there as
  a red canary job, not as a warning in every consumer's log — and newer
  than tested is the expected steady state between SDK releases, not an
  anomaly.

The gate runs once per handle family and is shared across every derived
handle (`with_config` never re-probes) — a benign race if two threads both
probe at once, since they write the same answer.

## What this SDK's own version promises you

Pre-1.0, `ocx-sdk` follows the same policy it applies to its wrapped
binary: **breaking changes ship without migration shims**, no deprecation
period, no compatibility layer. `MIN_SUPPORTED` may be raised in any minor
release. Every change is recorded in `CHANGELOG.md`, generated from
[Conventional Commits](https://www.conventionalcommits.org/) via
[git-cliff](https://git-cliff.org/) — that changelog, not this page, is the
authoritative record of what changed between releases.

Pin an exact version (`ocx-sdk==0.1.0`, not `ocx-sdk>=0.1`) if your
dependency management doesn't otherwise protect you from a pre-1.0 break.
