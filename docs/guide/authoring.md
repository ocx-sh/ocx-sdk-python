# Authoring packages

The author flow lives on
[`Ocx.package`](../reference/api.md#ocx_sdk.Ocx.package) — machine tier,
alongside `install`/`select`/`exec`, not on `Project`. Three methods carry
the core of it: [`create`](../reference/api.md#ocx_sdk.PackageCommands.create) →
[`test`](../reference/api.md#ocx_sdk.PackageCommands.test) →
[`push`](../reference/api.md#ocx_sdk.PackageCommands.push); the index side
— [`announce`](../reference/api.md#ocx_sdk.PackageCommands.announce),
[`claim`](../reference/api.md#ocx_sdk.PackageCommands.claim), and the
rolling-tag audit [`cascade_check`](../reference/api.md#ocx_sdk.PackageCommands.cascade_check) /
[`cascade_repair`](../reference/api.md#ocx_sdk.PackageCommands.cascade_repair)
— follows below.

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

The receipt itself is readable through
[`receipt`](../reference/api.md#ocx_sdk.PackageCommands.receipt) — a local
file read, no ocx process behind it — as a
[`BuildReceipt`](../reference/api.md#ocx_sdk.BuildReceipt): `None` when
`create` was given neither `identifier` nor `platform` and so wrote none,
`ValueError` naming the file when the sidecar is malformed or of a version
this SDK does not read. `receipt.ref` is the identifier as a
[`PackageRef`](../reference/api.md#ocx_sdk.PackageRef).

```python
from ocx_sdk import BuildReceipt

receipt = BuildReceipt.from_json('{"version": 1, "identifier": "ocx.sh/me/my-tool:1.0.0"}')
assert receipt.platform is None
assert str(receipt.ref) == "ocx.sh/me/my-tool:1.0.0"
```

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
    '{"status": "failed", '
    '"run": {"exit_code": 1, "stdout": "", "stderr": "", "duration_ms": 3, "truncated": false}, '
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
version.

## `announce` — publish the listing to the index

[`announce`](../reference/api.md#ocx_sdk.PackageCommands.announce) writes
the package's tag listing into the ocx index on a forge (GitHub or GitLab)
and returns an
[`AnnounceReport`](../reference/api.md#ocx_sdk.AnnounceReport) — `status`
is `"updated"` or `"unchanged"`, `pull_request_url` is set when the forge
path went through a PR, `capability_checks` records what the credential
was allowed to do. Exactly one tag source is required — `tags=`,
`tags_file=` (the file `cascade_repair(announce_tags=...)` writes) or
`tags_from_registry=True` — and ocx, not the SDK, enforces that: none or
two is a `UsageError` (64).

```python-no-run
# illustrative: needs a forge credential and network access.
from ocx_sdk import Ocx, OcxConfig

ocx = Ocx(config=OcxConfig(forge_token="glpat-..."))
report = ocx.package.announce("me/my-tool", tags=["1.0.0", "latest"])
print(report.status, report.pull_request_url)
```

A registry write like `push`, so `retry` defaults to `None`. The credential
comes from [`OcxConfig.forge_token`](../reference/api.md#ocx_sdk.OcxConfig)
(`OCX_ANNOUNCE_TOKEN`) or the ambient identity ladder described in
[Hermetic CI](hermetic-ci.md#the-forge-identity-ladder). `--yank`,
`--unyank`, `--fork` and `--out` are not typed: reach them through
[`invoke`](../reference/api.md#ocx_sdk.Ocx.invoke).

## `claim` — register a package with the index

[`claim`](../reference/api.md#ocx_sdk.PackageCommands.claim) registers a
package name and its owners in the index, returning a
[`ClaimReport`](../reference/api.md#ocx_sdk.ClaimReport). `repository` is
the OCI repository the claim points at; `owners=` lists forge logins beyond
the caller.

A claim is not idempotent on the wire: claiming an already-claimed package
exits 65 with no report, which the SDK surfaces as a plain
[`DataError`](../reference/api.md#ocx_sdk.DataError). The idempotent-CI
shape is to catch it and read the
[error envelope](concepts/errors-and-security.md#the-error-envelope):

```python-no-run
# illustrative: needs a forge credential and network access.
from ocx_sdk import DataError, Ocx, error_envelope

try:
    Ocx().package.claim("me/my-tool", repository="oci://ghcr.io/me/my-tool")
except DataError as exc:
    envelope = error_envelope(exc)
    if envelope is None or "already claimed" not in envelope.message:
        raise
```

## `cascade_check` / `cascade_repair` — audit the rolling tags

`push(cascade=True)` advances the rolling tags (`1`, `1.0`, `latest`) above
the version it publishes; a push without it leaves them behind.
[`cascade_check`](../reference/api.md#ocx_sdk.PackageCommands.cascade_check)
audits one or more repositories and
[`cascade_repair`](../reference/api.md#ocx_sdk.PackageCommands.cascade_repair)
rewrites what is stale. Both are **report-then-fail**: ocx exits 65 *with*
the report whenever it found something, and the SDK hands that report back
as a result rather than raising — the finding is the answer. `clean` is the
one-word verdict; `exit_code` keeps what ocx said.

```python
from ocx_sdk import CascadeCheckReport

report = CascadeCheckReport.from_json(
    '{"reports": [{"identifier": "ghcr.io/me/my-tool", "logical": null, "aliases": {}, '
    '"rows": [{"tag": "latest", "platform": {}, "status": "stale", "observed": null, "expected": null, '
    '"source": "1.0.1", "observed_source": "1.0.0"}], '
    '"index_findings": [], "ignored_tags": [], "unrepairable": []}]}',
    exit_code=65,
)
assert not report.clean
assert [(row.tag, row.status) for row in report.reports[0].rows] == [("latest", "stale")]
```

`cascade_repair(dry_run=True, announce_tags=path)` plans without writing and
leaves the tags it would touch in `path` — the file `announce(tags_file=path)`
consumes. Only a 65 *without* a report (an error envelope instead) raises.

## Consuming what you just published

The loop closes through the ordinary consumer surface —
[`package.install`](../reference/api.md#ocx_sdk.PackageCommands.install) or
a project's [`add`](../reference/api.md#ocx_sdk.Project.add) — pointed at
the identifier `push` returned. See
[Vendoring a dist.json](vendoring.md) for shipping a *bootstrap* manifest
snapshot inside a package, which is a separate concern from publishing the
package itself.
