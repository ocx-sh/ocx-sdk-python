---
title: ocx-sdk
hide:
  - navigation
---

# ocx-sdk

!!! warning "Pre-1.0 — API may change between minor versions"
    Breaking changes ship without migration shims. Pin to a version, watch the
    [release notes](https://github.com/ocx-sh/ocx-sdk-python/releases).

**Typed Python handles over [ocx](https://ocx.sh)** — the OCI-registry-backed
binary package manager. Bootstrap it anywhere, drive its toolchains, author
packages against it, all from Python: `ocx-sdk` wraps the CLI rather than
reimplementing it, so ocx keeps owning resolution and verification and you get
a typed, CWD-independent handle over the commands it exposes.

## At a glance

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **[Quickstart](guide/quickstart.md)**

    ---

    The canonical CI journey in one worked example: bootstrap, resolve a
    project, pull its toolchain, run inside it.

-   :material-book-open-variant:{ .lg .middle } **[Guide](guide/projects.md)**

    ---

    Bootstrap, projects & toolchains, hermetic CI, authoring packages,
    vendoring a `dist.json`, and the concepts underneath.

-   :material-api:{ .lg .middle } **[API reference](reference/api.md)**

    ---

    Auto-generated from docstrings. Every public symbol, its signature,
    parameters, and exceptions.

-   :material-hammer-wrench:{ .lg .middle } **[Contributing](contributing/index.md)**

    ---

    Bootstrap the toolchain, run the quality gate, write docs.

</div>

## Why a wrapper, not a reimplementation

ocx owns identifier resolution, checksum verification, and the registry
grammar. This SDK never re-derives any of that — it carries identifiers
byte-for-byte, parses the JSON ocx already validated, and gives you a typed
method for (almost) every command. Zero runtime dependencies, stdlib only,
100% test coverage, `py.typed` shipped in the wheel.

## See also

- :material-github: [Source on GitHub](https://github.com/ocx-sh/ocx-sdk-python)
- :material-package-variant: [`ocx` — the binary package manager](https://github.com/ocx-sh/ocx)
- :material-web: [OCX project portal](https://ocx.sh)
