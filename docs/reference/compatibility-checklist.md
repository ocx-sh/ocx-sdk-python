# Compatibility checklist

The durable-anchor checklist from the design record's compatibility policy
(design doc §3), with each anchor's named contract test (§14). **Re-verify
every row against ocx's own changelog whenever `TESTED_OCX_VERSION` bumps**
— a mismatch here is exactly the kind of pre-1.0 upstream break the tested
window exists to catch before a user does.

| Anchor | Named test | Tier |
|---|---|---|
| Exit-code taxonomy | `test_exit_code_taxonomy_fixtures` | contract |
| `version` plain output | `test_version_plain_output` | contract |
| File-schema URLs (`project/v1`, `project-lock/v3`, `metadata/v1`, `config/v1`) | n/a — no file-read feature ships in v0.1 | n/a |
| `launcher exec` wire ABI | n/a — no file-read feature ships in v0.1 | n/a |
| `package test --script` JSON ("stable v1 contract") | `test_package_test_envelope` | unit+acceptance |
| `$OCX_HOME/…/current/content/bin/ocx` stable install symlink path | `test_discovery_ocx_home_symlink` | unit |
| `ocx env --format json` typed-entry envelope | `test_env_wire_format_carries_declared_separators` | contract |
| `OCX_AUTH_<SLUG>_*` grammar | `test_registry_slug_fixtures` (mismatch fails closed) | contract |
| `login --password-stdin` | `test_login_password_stdin` | acceptance |
| Global `--project` flag | covered by `test_t1_result_shape_smoke` (every `Project` call injects it) | contract |
| `OCX_AUTH_*` child-propagation behavior | `test_auth_env_propagation_pinned` | contract |

The two `n/a` rows move to a real row, with a named test, the moment the
consuming feature (file reads) lands — until then there is nothing to pin
against a real binary.
