# `reports.v1.json`

ocx's own published contract for every `--format json` document, generated from
the Rust types the CLI serializes. `tests/unit/test_wire_contract.py` pins every
parser in `ocx_sdk._results` against it.

This file is **generated, never edited**. Refresh it from a checkout of
[`ocx-sh/ocx`](https://github.com/ocx-sh/ocx) at the version this SDK targets:

```sh
cargo run -p ocx_schema -- reports > .../tests/fixtures/contract/reports.v1.json
```

or take it from the published copy at <https://ocx.sh/schemas/reports/v1.json>.

## Why it exists

Every other payload in this suite is hand-written, which makes it evidence
about whoever typed it rather than about the wire. Three presence-rule
inversions reached `main` that way, one of which crashed `env()`. The generated
contract distinguishes the two `Option` shapes that look identical in a sample:

| ocx declares | serde writes | the parser must |
|---|---|---|
| `T` | always, non-null | `_need` |
| `Option<T>` | always, `null` when unset | `_need`, typed `\| None` |
| `Option<T>` + `skip_serializing_if` | absent when unset | `.get` |

A fixture only ever shows one of those, so it cannot tell them apart.
