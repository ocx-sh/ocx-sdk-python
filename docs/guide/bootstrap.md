# Bootstrap

[`bootstrap.ensure()`](../reference/api.md#ocx_sdk.ensure)
provisions a verified ocx binary and returns its path — the Python
equivalent of `curl -sSL https://setup.ocx.sh | sh`, minus any machine
mutation. It never touches a shell profile, never runs `self setup`, and
never writes anywhere outside its own cache directory.

```python-no-run
# illustrative: needs network access to fetch the dist manifest.
from ocx_sdk import Ocx, bootstrap

exe = bootstrap.ensure()          # latest stable
ocx = Ocx(exe=exe)
```

It is idempotent and offline-friendly: a cache hit that still hashes
correctly needs no network at all, and every knob resolves explicit
argument first, then the matching `OCX_INSTALL_*` variable, then a default.

Those variables are the same ones
`setup.ocx.sh` reads, so a machine already set up for
the shell installer needs no code change:

| Variable | Argument | Effect |
|---|---|---|
| `OCX_INSTALL_VERSION` | `version=` | Pin an exact release |
| `OCX_INSTALL_DIST_URL` | `dist=` | Where the manifest comes from (default source only) |
| `OCX_INSTALL_MIRROR_URL` | `mirror_url=` | Where the artifact bytes come from |
| `OCX_INSTALL_CA_BUNDLE` | `ca_bundle=` | PEM file trusted for the downloads |
| `OCX_INSTALL_FORCE` | — | Reinstall even on a cache hit |

`OCX_INSTALL_REPO` and `OCX_INSTALL_QUIET` are accepted and do nothing —
artifact URLs come from the manifest rather than a repository guess, and this
module prints nothing to quiet. Every variable is in
[Environment](../reference/environment.md#bootstrap-only-ocx_install_).

## Pinning

Pass `version=` for an exact release, or `channel=` to follow a channel's
latest (`Channel.STABLE` is the default; `Channel.NEXT` tracks pre-releases):

```python-no-run
# illustrative: needs network access.
from ocx_sdk import Channel, bootstrap

exe = bootstrap.ensure(version="0.6.0")
next_exe = bootstrap.ensure(channel=Channel.NEXT)
```

`min_version=` is the operator floor: a resolved version below it raises
`BootstrapError` instead of silently installing something older than the
caller allows.

## Corporate mirror + auth

A [`DistSource`](../reference/api.md#ocx_sdk.DistSource) is where the dist
manifest comes from — building one is pure and needs no network, so it is
safe to construct (and to unit test) without touching the wire:

```python
from ocx_sdk import BasicAuth, DistSource

source = DistSource.url(
    "https://dist.internal.example.com/ocx/dist.json",
    sha256="c" * 64,  # required off-canonical — see below
    auth=BasicAuth("ci", "hunter2"),
)
```

```python-no-run
# illustrative: feeding the source into ensure() needs network access.
from ocx_sdk import bootstrap

exe = bootstrap.ensure(dist=source, mirror_url="https://artifacts.internal.example.com/ocx")
```

`mirror_url=` relocates *where the artifact bytes come from* — the manifest
digest is still enforced, because a mirror moves bytes, it never revalidates
them. Setting `OCX_INSTALL_MIRROR_URL` in the environment does the same thing
without touching the call, which is how a CI image points every build at an
internal mirror; the explicit argument wins where both are set.

### Behind a TLS-intercepting proxy

`ca_bundle=` (or `OCX_INSTALL_CA_BUNDLE`) names a PEM file trusted for the
manifest and artifact downloads *instead of* the system store — the case
where an interception appliance presents a certificate no default trust store
knows:

```python-no-run
# illustrative: needs network access.
from ocx_sdk import bootstrap

exe = bootstrap.ensure(ca_bundle="/etc/ssl/corp/root.pem")
```

This is transport trust only. The manifest pin and the artifact digest are
untouched, so a bundle changes *who may serve* the bytes and never *which
bytes are accepted*. An unreadable or non-PEM file raises `DownloadError`
naming the variable, rather than failing later inside a fetch.

Ambient `HTTP_PROXY`/`HTTPS_PROXY` is honored without any SDK configuration:
the opener is built with `urllib.request.build_opener`, whose default
handler chain includes `ProxyHandler` unless explicitly replaced, and this
SDK only ever swaps in its own redirect and HTTPS-only handlers — the proxy
handler stays in the chain.

### The `sha256=` off-canonical rule

`sha256` on `DistSource.url`/`.path`/`.data` is the expected digest of the
manifest body itself. It is **required, fail-closed, whenever the host is
not the canonical `setup.ocx.sh` or a mirror is in play** — constructing a
source without it in that situation raises `DistManifestError` before any
network call happens. Talking to `setup.ocx.sh` directly is the one
exception, and even there a `dist/<sha256>.json` URL derives its own digest
automatically.

This is the SDK's trust boundary for the artifact that will eventually run
as a subprocess: a mirror can relocate bytes, but it cannot make the SDK
trust bytes nobody vouched for.

## `HostEnv` tiers

[`HostEnv`](../reference/api.md#ocx_sdk.HostEnv) is the environment snapshot
a spawned ocx (and, via bootstrap, the setup process) inherits from. Four
constructors, from most to least trusting:

```python
from ocx_sdk import HostEnv

ambient = HostEnv.ambient()          # os.environ, verbatim
minimal = HostEnv.minimal()          # PATH, HOME, TMPDIR (+ SYSTEMROOT, TEMP on Windows)
clean = HostEnv.clean()              # empty
narrowed = ambient.only("PATH", "HOME")
without_token = ambient.without("GITHUB_TOKEN")
```

`HostEnv.clean()` is hermetic and a documented footgun: a spawned tool loses
`PATH` and fails in ways that read like anything but a missing variable.
`HostEnv.minimal()` is the recovery — the platform-essential variables only,
with everything else, including any ambient credentials, left out.

```python
clean_child = HostEnv.clean()
assert clean_child.source == {}

recovered = HostEnv.minimal(windows=False)
assert set(recovered.source) <= {"PATH", "HOME", "TMPDIR"}
```

Pass a `HostEnv` to `Ocx(host_env=...)` (or `bootstrap.ensure(env=...)`) to
change what a handle's children inherit; `Ocx()` defaults to
`HostEnv.ambient()`. See [Hermetic CI](hermetic-ci.md) for the full
threat-model picture this is one lever of.

## What `ensure()` verifies

Every downloaded artifact is checked against the manifest's `sha256` before
it is ever executed. A mismatch raises `ChecksumMismatchError` and is
**never retried** — the bytes are wrong, not late. The cache directory
itself is hardened: a symlinked, foreign-owned, or group/other-writable root
is refused outright, and a cache hit re-hashes by default (`trust_cache=True`
opts out, for callers who have already verified the cache root some other
way).
