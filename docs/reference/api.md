# API reference

Auto-generated from docstrings via
[mkdocstrings](https://mkdocstrings.github.io/python/).

## Conventions

- Only names exported from the top-level `ocx_sdk` package (and the one
  public submodule, `ocx_sdk.bootstrap`) are public. Everything reachable
  through any other underscored module path is package-private and may
  change without notice — pre-1.0, breaking changes ship without shims.
- Docstrings follow [Google style](https://google.github.io/styleguide/pyguide.html#383-functions-and-methods).
- `Raises:` sections list every exception a method may raise from the SDK's
  own hierarchy; lower-layer exceptions are wrapped and preserved on
  `__cause__`.
- Every method mirrors one ocx command; see the [command ↔ method
  map](command-map.md) for the full correspondence, and
  [environment & exit codes](environment.md) for the wire-level detail
  behind the typed surface.

::: ocx_sdk
