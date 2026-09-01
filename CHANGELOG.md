# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-01

### Added

- Map the ocx 0.6.0 exit codes and keep a failed run's report *(errors)* **BREAKING**
- Neutralize ocx 0.6.0's kill switches and match ambient names by case *(env)* **BREAKING**
- Carry a failed run's stdout without ever printing it *(process)*
- Parse the ocx 0.6.0 report shapes and pin every presence rule *(results)* **BREAKING**
- Rename run to exec and bind the six new package commands *(client)* **BREAKING**
- Export the 0.2.0 API and pin it against the real binary *(surface)* **BREAKING**
- Honor OCX_INSTALL_CA_BUNDLE *(bootstrap)*

### Documentation

- Retarget the guide and reference at the ocx 0.6.0 surface
- Name every OCX_INSTALL_* variable where a reader looks *(bootstrap)*

## [0.1.0] - 2026-08-21

### Added

- Error taxonomy, retry policy, and the shared type vocabulary
- Hardened bootstrap — dist manifest, verified cache, binary discovery
- Spawn-env composition with secret redaction and the [env] merge
- Process lifecycle — one-shots, live handles, timeouts, stdin
- Typed client — Ocx, Project, and the package/config/patch namespaces

### Documentation

- Guide and reference site with executable snippets
[0.2.0]: https://github.com/ocx-sh/ocx-sdk-python/compare/v0.1.0..v0.2.0
[0.1.0]: https://github.com/ocx-sh/ocx-sdk-python/tree/v0.1.0

