<!-- doc_type: explanation -->

# Why a wrapper

The SDK drives the `ocx` binary instead of reimplementing resolution,
verification or the registry protocol in Python. This page gives the reasons
and the costs.

## What ocx keeps

ocx owns identifier resolution, signature verification, the registry
grammar and the object store. The SDK carries identifiers byte for
byte and parses the JSON that ocx already validated. When ocx fixes a verification bug or learns a registry quirk, SDK users
get the fix with the next binary. No SDK release is needed.

## What the SDK adds

One typed method per command, frozen result structs, and an exception tree
derived from the exit code. Nothing classifies a failure by matching stderr
text. The wheel has no runtime dependencies and ships a `py.typed` marker.
Unit tests hold 100% coverage, and the contract tier re-verifies the SDK's
model against the real binary.

## The one reimplemented algorithm

[`EnvReport.compose()`](../../reference/api.md#ocx_sdk.EnvReport.compose) folds
a toolchain's `[env]` entries into a mapping you can pass to your own
subprocess. That fold lives in Python because you need the result without
running a child. A contract test diffs it against `ocx exec -- printenv`, so a
divergence fails the build.

## The costs

- A call spawns a process, so it is slower than a library call.
- The SDK trails the binary. It refuses a binary below the supported
  floor and notes one above the tested version at debug level. See
  [Compatibility](compatibility.md).
- A command the SDK does not type stays reachable through
  [`Ocx.invoke`](../../reference/api.md#ocx_sdk.Ocx.invoke).

## See also

- [Errors and credentials](errors-and-security.md): how an exit code becomes an exception.
- [Command map](../../reference/command-map.md): which commands are typed.
