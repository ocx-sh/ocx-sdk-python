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
| `OCX_NO_CONSENT` refuses the project consent stamp | `test_consent_stamp_is_refused_by_default` | contract |
| Report-then-fail: `cascade check`/`repair` exit 65 *with* the report | `test_cascade_65_with_report_is_a_result` / `test_cascade_check_and_repair_round_trip` | unit / acceptance |
| Error envelope on a hard forge failure (`{schema_version, command, exit_code, error}`) | `test_a_forge_write_without_a_credential_carries_an_error_envelope` | contract |
| `package receipt` report — 0 with the record, 79 for none, 65 for unreadable | `test_receipt_round_trips_through_the_real_binary` | contract |
| Reports schema `reports/v1.json` — every parser's `_need`/`.get` read | `test_no_parser_reads_a_key_ocx_never_publishes` (vendored copy) / `test_vendored_reports_schema_matches_the_published_one` (nightly canary) | unit / acceptance |

Every row above was re-verified against ocx 0.6.1 on 2026-09-12, the bump
that added the last five.

The two `n/a` rows move to a real row, with a named test, the moment the
consuming feature (file reads) lands — until then there is nothing to pin
against a real binary.
