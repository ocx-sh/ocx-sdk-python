# Vendoring a `dist.json` snapshot

Every network-dependent `bootstrap.ensure()` starts by fetching a dist
manifest. A package that wants to bootstrap ocx without ever touching
`setup.ocx.sh` — an offline installer, an air-gapped CI image, a vendored
tool — ships a manifest **snapshot** as package data instead, and reads it
back with [`DistSource.data()`](../reference/api.md#ocx_sdk.DistSource.data).

## The pattern

1. Fetch (or receive from an operator) the manifest bytes once, and record
   their sha256 — the filename *is* the integrity check, so name the file
   after the digest it carries.
2. Ship it as package data at `dist/<sha256>.json`.
3. At runtime, read it back with `importlib.resources` — which works from a
   zipped wheel, where a plain filesystem path does not — and hand the bytes
   to `DistSource.data()`.

```python
import hashlib

from ocx_sdk import DistSource

# Step 1, done once when the snapshot is captured — not at runtime.
raw = b'{"latest": {"channel": "stable", "version": "0.5.8"}, "releases": []}'
digest = hashlib.sha256(raw).hexdigest()
snapshot_filename = f"dist/{digest}.json"

# Step 3, at runtime — `raw` here stands in for importlib.resources.read_binary(...).
source = DistSource.data(raw, sha256=digest)
```

```python-no-run
# illustrative: the real runtime read, from a package's own resources.
from importlib.resources import files

from ocx_sdk import DistSource, bootstrap

raw = files("my_package").joinpath("dist/424b6351...json").read_bytes()
exe = bootstrap.ensure(dist=DistSource.data(raw, sha256="424b6351..."))
```

## Why the filename is the refresh check

`DistSource.data()` validates `sha256` against the bytes it was given at
construction time — a mismatch raises `DistManifestError` before any
manifest parsing happens. Naming the shipped file after that same digest
means a snapshot that drifted from what the filename claims fails loudly at
build or test time, not silently at install time on someone else's machine.
Refreshing the snapshot is then just: fetch new bytes, compute the new
digest, rename the file, update the `sha256=` you pass at the call site —
three of those four steps are mechanical enough to script.

## What this buys, and what it doesn't

A vendored snapshot removes the network dependency for *resolving which
release to install* — `DistSource` only ever supplies the manifest. The
artifact download and its own checksum verification still happen over the
network (or from ocx's own cache) when `ensure()` runs; vendoring the
manifest does not vendor the binary. Combine it with `mirror_url=` (see
[Bootstrap](bootstrap.md#corporate-mirror-auth)) to relocate the artifact
fetch too.
