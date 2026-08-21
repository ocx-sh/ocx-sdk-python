# Authoring packages

The author flow lives on
[`Ocx.package`](../reference/api.md#ocx_sdk.Ocx.package) — machine tier,
alongside `install`/`select`/`exec`, not on `Project`. Three methods carry
it: [`create`](../reference/api.md#ocx_sdk.PackageCommands.create) →
[`test`](../reference/api.md#ocx_sdk.PackageCommands.test) →
[`push`](../reference/api.md#ocx_sdk.PackageCommands.push).

```python-no-run
# illustrative: needs a real ocx binary and a real package directory.
from ocx_sdk import Ocx

ocx = Ocx()
ocx.package.create("./my-tool", identifier="ocx.sh/me/my-tool:1.0.0", platform="linux/amd64")
result = ocx.package.test(
    "ocx.sh/me/my-tool:1.0.0",
    script="./test.star",
    metadata="./my-tool/metadata.json",
)
if result.passed:
    published = ocx.package.push("./my-tool.tar", identifier="ocx.sh/me/my-tool:1.0.0")
    print(published.manifest_digest)
```

## `create` — bundle a directory

[`create`](../reference/api.md#ocx_sdk.PackageCommands.create) bundles a
local directory into a package archive, writing a build receipt beside it
that `test` and `push` read back — `identifier` and `platform` given here
don't need repeating on the later calls. There is nothing to return: ocx
prints no payload for this command.

## `test` — the `--script` envelope

Only the `--script` form of `ocx package test` is typed. The trailing
`-- CMD` form prints the tested command's raw stdout verbatim, even under
`--format json`, so nothing here could parse it reliably — reach for
[`invoke`](../reference/api.md#ocx_sdk.Ocx.invoke) if you need that form.

The `--script` form runs a Starlark test script against a materialized copy
of the package and returns a
[`TestResult`](../reference/api.md#ocx_sdk.TestResult) — the **stable v1
envelope**, one of the durable anchors re-verified on every ocx version
bump. `status` decides pass or fail; when it fails,
`assertion.kind` is the stable, machine-readable reason (`assertion` is
`None` on a pass):

```python
from ocx_sdk import TestResult

result = TestResult.from_json(
    '{"status": "failed", "run": {"exit_code": 1}, '
    '"assertion": {"kind": "exit_code_mismatch", "message": "expected 0, got 1"}}'
)
assert not result.passed
assert result.assertion is not None
assert result.assertion.kind == "exit_code_mismatch"
```

`layers=` takes layer archives or digest references, base first; `metadata=`
is required whenever no file layers are given; `private=True` composes
ocx's `--self` surface for testing a package's own private tooling.

## `push` — publish, deliberately not retried by default

[`push`](../reference/api.md#ocx_sdk.PackageCommands.push) publishes a
package's layers and metadata to a registry and returns a
[`PushResult`](../reference/api.md#ocx_sdk.PushResult) — the published
identifier, digest, and tags.

Like [`Ocx.login`](../reference/api.md#ocx_sdk.Ocx.login), `push` defaults
its per-call `retry` to `None` regardless of session policy: a push is a
registry write, and re-sending one after a timeout risks publishing twice.
Pass `retry=` explicitly when the target registry is known to be
idempotent-safe. `cascade=True` also advances the rolling tags above this
version; `new=True` skips the checks that expect an existing index, for a
package's first publish.

## Consuming what you just published

The loop closes through the ordinary consumer surface —
[`package.install`](../reference/api.md#ocx_sdk.PackageCommands.install) or
a project's [`add`](../reference/api.md#ocx_sdk.Project.add) — pointed at
the identifier `push` returned. See
[Vendoring a dist.json](vendoring.md) for shipping a *bootstrap* manifest
snapshot inside a package, which is a separate concern from publishing the
package itself.
