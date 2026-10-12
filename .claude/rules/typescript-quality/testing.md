---
title: Testing
summary: The TS-TEST family — runner choice per shape, determinism, and the coverage, type-test and packaging gates that silently pass forever
---

# Testing

Owns `TS-TEST`: which runner a directory gets, what makes a test result
reproducible, and every way a TypeScript test gate reads green while checking
nothing. It does not own test doubles and casts — the double-cast ban is
`TS-TYP-06`, and the fake shapes are `TS-HOST-14/16/24/25/26`, none of it
restated here.

Contents: [Choosing the Runner](#choosing-the-runner) ·
[Coverage That Can Actually Fail](#coverage-that-can-actually-fail) ·
[Determinism](#determinism) ·
[Gates That Prove a Contract](#gates-that-prove-a-contract) ·
[What Agents Get Wrong Here](#what-agents-get-wrong-here)

Severity maps onto the house tiers: MUST = Block, SHOULD = Warn,
CONSIDER = Suggest. Every command lists its files with `git ls-files`, which respects
`.gitignore`; set `TEST_ROOT` to the directory the runner actually
collects from. Version-bound claims were measured 2026-08-29 and name the
release they bind.

## Choosing the Runner

The check for this whole block: open the nearest `package.json` and the test
config beside it, and write against what is already there. More than one
runner in one *repository*, split by test tier — pure logic, component, end
to end — is a deliberate shape, not drift. Two runners collecting the same
*directory* is drift.

| ID | Rule | Rationale | Verification | Severity |
|---|---|---|---|---|
| TS-TEST-01 | Write every test against the runner already declared in the nearest `package.json`, and never add a second runner to obtain one helper — a mocking API, a matcher, a fixture style. Where the target directory has no runner at all, take the one from the shape table below. | Every deep-partial mock factory and every `expectTypeOf` implementation is bound to one runner's ecosystem, so "just add the mock library" quietly means adding its runner, its config, and a second CI invocation — to replace a fake worth fifteen lines. Where no compatible library exists, the named-helper shape (`TS-HOST-14`) is the terminus, not a fifth runner. | see block TS-TEST-01 — a union of three; every hit whose API is not the runner declared in that directory's `package.json` is the violation. Then `npm ls --depth=0` there: two runners listed, both collecting the same files, is the finding. | MUST |
| TS-TEST-02 | Await every assertion from a browser-automation runner. | The web-first `expect` returns a promise and auto-retries until its timeout; unawaited, the assertion never runs and the test passes against an element that was never on the page. The same matcher names exist on a unit runner's synchronous `expect`, so a line copied between the two suites reads identical and silently stops retrying. | see block TS-TEST-02 — a top-level `expect(` with no `await` in front is the violation, empty output is the pass. The durable fix is typescript-eslint's `no-floating-promises`, which is inert until typed linting is wired (`TS-TYP-01`). | MUST |
| TS-TEST-03 | An editor/Electron extension-host suite sets an explicit `mocha.timeout` in its test config, above Mocha's 2000 ms default, and any hook that boots the host sets its own. | The host launches a real editor before the first test runs. The default clears that on a warm Linux runner and misses it on a cold macOS one, so the suite is green locally and red in CI only sometimes — the failure that gets re-run rather than read. A `timeout` on the config covers tests, not a teardown hook that sets none. | Read the `mocha` block of the extension test config (`.vscode-test.mjs` or equivalent): no `timeout` key is the violation. | SHOULD |

```sh
# TS-TEST-01
TEST_ROOT=tests    # the directory to search
git ls-files -z --cached --others --exclude-standard -- ":(glob)$TEST_ROOT/**/*.test.ts" ":(glob)$TEST_ROOT/**/*.spec.ts" | xargs -0 -r grep -H -n -E -e '(^|[^[:alnum:]_])jest\.' -e '(^|[^[:alnum:]_])vi\.' -e "from 'bun:test'" --
```

```sh
# TS-TEST-02
E2E_ROOT=e2e    # the e2e root under check
git ls-files -z --cached --others --exclude-standard -- ":(glob)$E2E_ROOT/**/*.spec.ts" ":(glob)$E2E_ROOT/**/*.e2e.ts" | xargs -0 -r grep -H -n -E -e '^[[:space:]]*expect\(' --
```

```ts
expect(page.getByRole('alert')).toHaveText('saved');        // never runs; test is green
await expect(page.getByRole('alert')).toHaveText('saved');  // retries until timeout
```

**Pinned default — the adopter names their own.** The runner per shape, and
the single fact that decides each:

| Shape | Runner | The fact that decides it |
|---|---|---|
| Anything already transformed by Vite/Rolldown | Vitest | shares the app's own transform pipeline; no second build config to drift |
| A package built and run by Bun | `bun test` | native TS transform, Jest-shaped API, zero added dependency |
| An editor/Electron extension host | Mocha plus the vendor's extension test CLI | the only harness that boots the real host; it has no ESM or TS loader, so test files need a bundle step before the runner sees them |
| Browser end-to-end and visual regression | Playwright | isolates every individual test; a unit runner's browser mode isolates only per *file* |
| A zero-dependency script | `node:test` | free with Node — but its coverage, tags and global setup/teardown are all still Stability 1 as of Node 26 (2026-08), so its thresholds are not a gate |

## Coverage That Can Actually Fail

The check for this block, once per repository: break the tree on purpose —
delete an assertion, add an untested file — and confirm the gate exits
non-zero. A coverage config nobody has watched go red is decoration.

| ID | Rule | Rationale | Verification | Severity |
|---|---|---|---|---|
| TS-TEST-04 | Copy a coverage-threshold block from the runner's current documentation key for key, and prove it red once before trusting it. Never let a single metric be the only threshold. | No runner errors on an unrecognized threshold key — a misspelling is treated as unset, so the gate prints a percentage, exits 0 forever, and is indistinguishable from a passing gate. Named instance: Bun's keys are plural (`lines`, `functions`, `statements`), the singular forms are accepted and ignored, and `statements` is documented as accepted but never enforced even when spelled right. Bun also skips threshold enforcement on an lcov-only run, which exits 0 regardless of the numbers. | see block TS-TEST-04 and check each key against the runner's own docs. Then raise one threshold above the tree's real coverage and run the gate: exit code 0 is the violation. | MUST |
| TS-TEST-05 | On Vitest 4.0 and later (2025-10-22), set `coverage.include` explicitly. | v4.0 removed `coverage.all`; coverage now reports only files the run actually loaded, so a source file no test imports is absent from the report rather than present at 0% — and the percentage goes **up** when coverage gets worse. `coverage.exclude` does not close this; the include list is what makes an untested file visible. | see block TS-TEST-05 — no `include` key under `coverage` is the violation. Confirm live: add a source file nothing imports, run coverage, and require it to appear at 0%. Missing from the report is the finding. | MUST |

```sh
# TS-TEST-04
CONFIG_FILE=vitest.config.ts    # the file to search
CHECKS=.claude/rules/typescript-quality/checks    # where this rule's checks/ directory is installed
PAT=coverageThreshold AFTER=3 BEFORE=0 awk -f "$CHECKS/context.awk" -- "$CONFIG_FILE"
```

```sh
# TS-TEST-05
CHECKS=.claude/rules/typescript-quality/checks    # where this rule's checks/ directory is installed
git ls-files -z --cached --others --exclude-standard -- 'vitest.config.ts' | PAT=coverage AFTER=10 BEFORE=0 xargs -0 -r awk -f "$CHECKS/context.awk" --
```

## Determinism

The check for this block: run the suspect file alone, then run the whole
suite. A test that passes in exactly one of the two modes is the violation,
in either direction, and the run names it.

| ID | Rule | Rationale | Verification | Severity |
|---|---|---|---|---|
| TS-TEST-06 | A test that asserts on microtask ordering names the APIs it fakes. Vitest's `vi.useFakeTimers()` is `@sinonjs/fake-timers`-backed and does **not** fake `process.nextTick` or `queueMicrotask` unless they are listed in `toFake`. | Faking the macrotask clock and assuming microtasks came along produces a test that passes because the ordering it claims to check never ran under fake time at all. `nextTick` faking additionally does not work under `--pool=forks`, only `--pool=threads`, so the same test changes behaviour on a pool change nobody connected to it. | see block TS-TEST-06 (1); for each call with no `toFake`, run see block TS-TEST-06 (2) — a hit is the violation. | SHOULD |
| TS-TEST-07 | A snapshot asserts a named fact: an inline snapshot or a direct property assertion, never one snapshot of a whole object or a whole rendered tree. Every pixel snapshot sets an explicit tolerance and pins its browser and theme matrix. | A broad snapshot is not read on review, so updating it becomes a reflex and it stops being an assertion. A pixel snapshot on the defaults compares font rendering and anti-aliasing that differ per CI runner — that is a flake, not a regression, and it trains everyone to pass `-u`. | see block TS-TEST-07 — a call with no options object is the violation. Then, in any diff: a changed snapshot file with no change to rendering code is a blanket update, and is the finding. | SHOULD |
| TS-TEST-08 | A browser-mode unit test undoes every global, DOM and module mutation it makes, in an `afterEach` in the same file. | A browser-mode runner opens one page per test *file*, not per test, so state a test leaves behind is visible to the next test in that file and to nothing else. The suite passes, the single test fails when run alone, and the diagnosis lands months later on whoever changed test order. | Run the file, then run one test in it alone by name filter. Disagreement between the two runs is the violation. | SHOULD |
| TS-TEST-09 | Declare anything a `vi.mock` factory closes over from module scope with `vi.hoisted()`. | `vi.mock` is hoisted above every import, so a `const` written above it in source order does not exist yet when the factory runs. It surfaces as a mocking error far from the mistake, and the first fix an agent reaches for — moving the `const` up — cannot work. | see block TS-TEST-09, then read each factory body: an identifier that is neither imported above the mock call nor produced by `vi.hoisted()` is the violation. | SHOULD |

```sh
# TS-TEST-06 (1)
TEST_ROOT=tests    # the directory to search
THAT_FILE=test/x.test.ts    # the file to search
CHECKS=.claude/rules/typescript-quality/checks    # where this rule's checks/ directory is installed
git ls-files -z --cached --others --exclude-standard -- ":(glob)$TEST_ROOT/**" | PAT=useFakeTimers AFTER=6 BEFORE=2 xargs -0 -r awk -f "$CHECKS/context.awk" --
# TS-TEST-06 (2)
grep -H -n -E -e nextTick -e queueMicrotask -- "$THAT_FILE"
```

```sh
# TS-TEST-07
TEST_ROOT=tests    # the directory to search
git ls-files -z --cached --others --exclude-standard -- ":(glob)$TEST_ROOT/**/*.test.ts" ":(glob)$TEST_ROOT/**/*.spec.ts" | xargs -0 -r grep -H -n -E -e 'toHaveScreenshot\(\)' -e 'toMatchScreenshot\(\)' --
```

```sh
# TS-TEST-09
TEST_ROOT=tests    # the directory to search
CHECKS=.claude/rules/typescript-quality/checks    # where this rule's checks/ directory is installed
git ls-files -z --cached --others --exclude-standard -- ":(glob)$TEST_ROOT/**/*.test.ts" | PAT='vi\.mock\(' AFTER=6 BEFORE=0 xargs -0 -r awk -f "$CHECKS/context.awk" --
```

## Gates That Prove a Contract

The check for this block: the CI job list must actually invoke the tool.
Present in `devDependencies` and wired into no script is the common state,
and it is indistinguishable from not installed.

| ID | Rule | Rationale | Verification | Severity |
|---|---|---|---|---|
| TS-TEST-10 | Every package without `"private": true` runs a packaging-contract check against a **packed tarball** in CI — `attw --pack .` with the profile matching its real target runtimes, plus `publint`. | Those are the bytes a stranger's bundler resolves against. A broken `exports` map, or a declaration format that fails under one resolution mode, is an install break for every consumer, and `tsc` never sees it because `tsc` reads the source tree. `publint`'s programmatic API returns messages, so the check can be an assertion inside the existing suite instead of an advisory report nobody reads. | see block TS-TEST-10 (1) lists the packages this binds. For each, see block TS-TEST-10 (2) must hit; no hit is the violation whether or not the tools are installed. | MUST |
| TS-TEST-11 | Wire type-level assertions into an invocation that actually compiles them — `vitest --typecheck`, or a `tsc --noEmit` whose `include` covers the type-test files. | Type assertions do not run themselves. Vitest statically analyzes `*.test-d.ts` files and never executes them, so without `--typecheck` they are collected and reported as passing without being compiled. Bun documents `expectTypeOf` as a **runtime no-op**, so a Bun suite full of type assertions is green by construction. Either way a whole directory of type tests proves nothing inside a suite that says it is green. | see block TS-TEST-11 (1) lists the type tests; if it lists anything, see block TS-TEST-11 (2) must hit. Then break one assertion and require the run to go red — this is the one rule whose verification is worthless unless it has been watched fail. | MUST |
| TS-TEST-12 | Assert type identity with `toEqualTypeOf` and assignability with `toExtend`; add `.toBeAny()` wherever an `any` leak is what the test guards against. A case that must NOT compile is `@ts-expect-error` on the line, never a prose comment saying it should error. | `any` is assignable in both directions, so a function that has degraded to returning `any` passes a naive identity assertion — `.toBeAny()` is the only matcher that catches it. An unused `@ts-expect-error` is itself a compile error, so a negative test self-invalidates the moment the guarded case stops failing; a comment rots in silence. | see block TS-TEST-12 — deprecated since expect-type 1.2.0 (2025-02-28), still the first name a model reaches for, and every hit is stale. Directive discipline itself is `TS-TYP-03`. | SHOULD |
| TS-TEST-13 | Check schema-to-type agreement with exact equality in both directions. Never `satisfies SchemaType<T>` as the only check. | `satisfies` is one-directional assignability: it catches a missing required key and lets an extra key, an omitted optional and a bare `any` field all through — the schema then drifts from the type it exists to enforce, in the direction that ships bad data. Zod's own docs state this about `satisfies z.ZodType<T>`; it generalizes to every validation library, because the blind spot is in `satisfies`, not in the library. | see block TS-TEST-13 — a hit with no exact-equality assertion (`toEqualTypeOf`, or the library's own helper) covering the same schema is the violation. | SHOULD |

```sh
# TS-TEST-10 (1)
PACKAGES_DIR=packages    # the directory to search
CI_WORKFLOW_DIR=.github/workflows    # the directory to search
git ls-files -z --cached --others --exclude-standard -- ":(glob)$PACKAGES_DIR/**/package.json" | xargs -0 -r grep -L -E -e '"private": true' --
# TS-TEST-10 (2)
git ls-files -z --cached --others --exclude-standard -- ":(glob)$CI_WORKFLOW_DIR/**" | xargs -0 -r grep -H -n -E -e attw -e publint --
```

```sh
# TS-TEST-11 (1)
CI_WORKFLOW_DIR=.github/workflows    # the directory to search
git ls-files --cached --others --exclude-standard -- ':(glob)**/*.test-d.ts'
# TS-TEST-11 (2)
git ls-files -z --cached --others --exclude-standard -- 'package.json' ":(glob)$CI_WORKFLOW_DIR/**" | xargs -0 -r grep -H -n -E -e typecheck -e noEmit --
```

```sh
# TS-TEST-12
TEST_ROOT=tests    # the directory to search
git ls-files -z --cached --others --exclude-standard -- ":(glob)$TEST_ROOT/**/*.ts" | xargs -0 -r grep -H -n -E -e toMatchTypeOf --
```

```sh
# TS-TEST-13
SRC_DIR=src    # the directory to search
git ls-files -z --cached --others --exclude-standard -- ":(glob)$SRC_DIR/**/*.ts" | xargs -0 -r grep -H -n -E -e 'satisfies z\.' -e 'satisfies .*Schema' --
```

## What Agents Get Wrong Here

1. **Reaching for a mocking library and bringing a runner with it.** The
   deep-partial mock factories are runner-bound; adding one to a Mocha or
   `node:test` tree means a whole extra runner to avoid writing a fake.
2. **Reporting a coverage percentage as evidence** without having watched
   the threshold fail. An ignored key and a passing gate print the same
   thing.
3. **Copying an `expect` between an end-to-end file and a unit file.** The
   matcher names match, the retry semantics do not, and the copy without
   `await` asserts nothing at all.
4. **Adding `*.test-d.ts` files without adding `--typecheck`.** The suite
   grows, the compiler never reads them, and the run stays green.
5. **Naming a package or a flag that was retired.** `@vitest/browser` (split
   into per-provider packages at Vitest 4.0, 2025-10-22),
   `@playwright/experimental-ct-*` (replaced inside `@playwright/test` at
   1.62, 2026-07-24) and `toMatchTypeOf` are all over training data, and all
   still install or parse without error.
6. **Writing `enum` or `namespace` in a file meant to run directly under
   `node --test`.** Node strips types, it does not compile them; those
   constructs throw `ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX`.
7. **Updating a snapshot to make a suite green** on a change that touched no
   rendering code.
8. **Assuming `useFakeTimers()` faked everything with a clock in it.** The
   two APIs it skips are the two a microtask-ordering test depends on.
