# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OCX Authors

"""Typed handles over the ocx command line (contract C-011).

`Ocx` is the entry point: one handle, one binary, one configuration. Its
methods mirror ocx commands one for one — `ocx.about()` runs `ocx about`,
`ocx.project(path).lock()` runs `ocx lock --project <path>`, and
`ocx.package.install(...)` runs `ocx package install`. Anything the SDK does
not type is still reachable through `invoke`.

Three tiers, matching ocx's own split:

- **Machine tier** — `Ocx` itself and the `package`, `config`, `patch`
  namespaces. They operate on `$OCX_HOME` and take no project path.
- **Project tier** — `Ocx.project(path)` returns a `Project` whose every call
  carries `--project <path>`, so no method depends on the working directory.
- **Raw** — `invoke`/`spawn` compose the configured globals and the child
  environment, then get out of the way.

Handles hold no mutable state (design §7): the only construction-time
snapshot is the resolved binary path, and the only memo is the compatibility
gate, shared with every handle derived through `with_config`. All three
handle kinds carry the same private `_Runner`, which owns argv composition,
the child environment, and that gate — so the machinery has one home and no
handle reaches into another.

Only two things reach argv from `OcxConfig`: `log_level`, which has no
environment tier the SDK writes, and `--project`, which must be explicit
because the ambient `OCX_PROJECT` is neutralized on every spawn. Every other
field travels as an `OCX_*` variable composed by `_env.build_spawn_env` —
one config-to-wire mapping, in the module that owns it.

Presentation is pinned where output is parsed and left alone where it is
not: typed methods add `--format json --color never`; `version()` reads the
plain stable contract; `run`/`exec` keep stdout for the child they host; and
`invoke` pins nothing at all.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, Never, Unpack

from . import _bootstrap
from ._config import ConfigOverrides, OcxConfig
from ._env import build_spawn_env, serialize_env_value
from ._errors import VersionCompatError
from ._process import compose_argv, run_command, run_command_async, spawn, spawn_async
from ._results import (
    AboutInfo,
    CommandResult,
    ConfigSetupReport,
    ConfigUpdateReport,
    DepsReport,
    EnvReport,
    InfoResult,
    InspectReport,
    InstallReport,
    LoginResult,
    LogoutResult,
    PullReport,
    PushResult,
    RemovalResult,
    StatusReport,
    TestResult,
    ToolRow,
    WhichResult,
    parse_info,
    parse_package_pull,
    parse_removals,
    parse_tool_rows,
    parse_which,
)
from ._types import MIN_SUPPORTED, TESTED_OCX_VERSION, EnvValue, HostEnv, PackageLike, RetryPolicy

if TYPE_CHECKING:
    import asyncio
    import subprocess
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

    from ._env import SpawnEnv
    from ._process import Completed

_LOG: Final = logging.getLogger("ocx_sdk")
"""SDK events — the compat gate's newer-than-tested note lands here."""

_SEMVER_CORE: Final = re.compile(r"\d+(?:\.\d+)*")
"""The dotted numeric head of a version, ignoring any pre-release tail."""

_JSON: Final = ("--format", "json", "--color", "never")
"""Presentation for a typed call whose stdout this module parses."""

_PLAIN: Final = ("--color", "never")
"""Presentation where stdout is a stable plain contract or a child's output."""

_RAW: Final[tuple[str, ...]] = ()
"""No presentation at all — `invoke`/`spawn` leave the choice to the caller."""

_PROJECT_FILE: Final = "ocx.toml"
"""The filename ocx's CWD walk looks for, and what a directory resolves to."""

_PROJECT_SUFFIX: Final = ".toml"
"""What names a project *file* on a path that does not exist yet."""

_RESERVED: Final = {
    "run": "the toolchain runner is `ocx.project(...).run(argv)`, and raw argv is `ocx.invoke(argv)`",
    "exec": "running inside packages is `ocx.package.exec(packages, argv)`",
}
"""Command names people reach for on the wrong handle, and where they live."""

type LazyMode = Literal["never", "always"]
"""When a tool's content downloads: eagerly, or on first use."""

type Resolve = Literal["candidate", "current"]
"""Which `$OCX_HOME` symlink a package-tier call resolves through.

`'candidate'` is the installed version's own link, `'current'` the selected
one. Omitting it leaves the choice to ocx, which is the usual answer.
"""


class _Unset(Enum):
    """The sentinel type for "the caller said nothing"."""

    TOKEN = "unset"


UNSET: Final = _Unset.TOKEN
"""The default for every per-call `retry=`/`timeout=`, meaning "not given".

Public so that a wrapper around this SDK can pass a caller's override
straight through without having to invent its own three-state sentinel:
`def deploy(*, retry: MaybeRetry = UNSET): ocx.package.push(..., retry=retry)`.
"""

type MaybeRetry = RetryPolicy | Literal[_Unset.TOKEN] | None
"""A per-call retry override: a policy, `None` to opt out, or `UNSET` to take the config's."""

type MaybeTimeout = float | Literal[_Unset.TOKEN] | None
"""A per-call timeout override: seconds, `None` to wait forever, or `UNSET` to take the config's."""


class _CompatCell:
    """The one-slot memo behind the compatibility gate.

    Shared by reference across every handle derived from one construction, so
    a `with_config` chain probes the binary once rather than once per handle.
    The race is benign: two threads may both probe and both write the same
    answer.
    """

    __slots__ = ("checked",)

    def __init__(self) -> None:
        self.checked = False


class Ocx:
    """A handle on one ocx binary.

    Construction resolves the binary once — the explicit `exe`, then
    `OCX_SDK_EXE`, then `PATH`, then ocx's own install symlink — and pins the
    result for the handle's lifetime. Every call composes its own argv and
    child environment, and the handle exposes no mutators, so it is safe to
    share across threads, tasks, and event loops.

    The first typed call probes `ocx version` and refuses a binary older than
    `MIN_SUPPORTED`; newer than `TESTED_OCX_VERSION` is expected and only
    noted at DEBUG.

    A long-lived handle keeps running the binary it resolved, so a consumer
    shaped like a daemon should reconstruct `Ocx()` periodically to pick up
    what an `ocx self update` replaced underneath it.

    Example:
        ```python
        from ocx_sdk import Ocx, OcxConfig

        ocx = Ocx(config=OcxConfig(offline=True))
        project = ocx.project("/srv/build")
        project.pull()
        result = project.run(["task", "verify"])
        ```

    Attributes:
        exe: The resolved binary this handle runs.
        session_config: The `OcxConfig` every call spawns under.
    """

    __slots__ = ("_runner",)

    def __init__(
        self,
        exe: str | Path | None = None,
        *,
        config: OcxConfig | None = None,
        host_env: HostEnv | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        """Resolve a binary and pin it to this handle.

        Args:
            exe: An explicit binary path. `None` discovers one.
            config: Session configuration. `None` uses the defaults.
            host_env: The environment children inherit from. `None` snapshots
                the ambient one at construction. Named apart from the `env=`
                on `run`/`env()`/`inspect()`, which takes `[env]` entries for
                one call and is a different thing entirely.
            on_log: Called with each redacted stderr line from the pump
                thread, so it must be thread-safe. Rejected on async paths.

        Raises:
            OcxNotFoundError: No binary was found; the message names
                `bootstrap.ensure()` as the fix.
        """
        host = HostEnv.ambient() if host_env is None else host_env
        self._runner = _Runner(
            _bootstrap.discover(exe, host).resolve(),
            OcxConfig() if config is None else config,
            host,
            on_log,
            _CompatCell(),
        )

    @classmethod
    def _derive(cls, runner: _Runner) -> Ocx:
        """Build a handle around an existing runner.

        The private alt-constructor `with_config` derivation goes through:
        discovery runs in `__init__` and nowhere else, so a derived handle
        can never pick a different binary than the one it came from.
        """
        handle = object.__new__(cls)
        handle._runner = runner
        return handle

    @property
    def exe(self) -> Path:
        """The resolved binary path, fixed at construction."""
        return self._runner.exe

    @property
    def session_config(self) -> OcxConfig:
        """The `OcxConfig` this handle spawns under.

        Read-only, and the config itself is frozen: derive a variant with
        `with_config(...)` rather than trying to write through this. Not
        spelled `config` because `ocx.config` is the `ocx config` command
        group, and command alignment wins over the shorter name.
        """
        return self._runner.config

    def __repr__(self) -> str:
        return f"Ocx(exe={str(self._runner.exe)!r})"

    # Hidden from the type checker on purpose. A visible `__getattr__` tells
    # pyright that *every* attribute name resolves, which silently retires
    # unknown-attribute reporting for the SDK's central handle — a far bigger
    # loss than the runtime hint below is a gain. Runtime keeps the hint.
    if not TYPE_CHECKING:  # pragma: no branch - always taken at runtime

        def __getattr__(self, name: str) -> Never:
            """Fail a missing attribute, with a pointed hint for the two traps.

            `ocx.run` and `ocx.exec` are the names people reach for first, and
            both belong somewhere else: `run` is project-tier, `exec` is
            package-tier.

            Args:
                name: The attribute that was not found.

            Raises:
                AttributeError: Always. Command names are reserved for the
                    methods that wrap them.
            """
            hint = _RESERVED.get(name)
            if hint is not None:
                raise AttributeError(f"Ocx has no attribute {name!r}: {hint}.")
            raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def with_config(self, **overrides: Unpack[ConfigOverrides]) -> Ocx:
        """Derive a handle with some configuration fields replaced.

        The derived handle shares this one's binary, host environment,
        `on_log`, and compatibility memo — only the configuration differs.
        Changing any of the others means constructing a fresh `Ocx`.

        Args:
            **overrides: `OcxConfig` field names and their new values, as
                `ConfigOverrides` spells them.

        Returns:
            The derived handle.

        Raises:
            TypeError: An override names no `OcxConfig` field. A type checker
                catches that first; the runtime guard is for callers who
                build the keywords dynamically.
        """
        return Ocx._derive(self._runner.derive(**overrides))

    def project(self, path: str | Path) -> Project:
        """Return a project-tier handle rooted at `path`.

        The path is made absolute immediately, so the handle keeps targeting
        the same project no matter what the working directory does later.

        ocx's `--project` names the project *file*, the way Cargo's
        `--manifest-path` does, and accepts any filename. A directory is the
        obvious thing to pass and the common case, so it is resolved to the
        `ocx.toml` inside it.

        A path that does not exist yet — which is every path `init()` is
        called on — is read by its name: a `.toml` suffix means the file,
        anything else means the directory. Guessing "file" there would make
        `project('/srv/build').init()` write `/srv/ocx.toml`, one level above
        where the caller pointed.

        Args:
            path: The project directory, or the project file directly.

        Returns:
            A handle whose every call carries `--project <file>`.
        """
        resolved = Path(path).resolve()
        names_the_file = resolved.suffix == _PROJECT_SUFFIX or resolved.is_file()
        return Project(self._runner, resolved if names_the_file else resolved / _PROJECT_FILE)

    @property
    def package(self) -> PackageCommands:
        """The `ocx package` command group — machine tier, no project path."""
        return PackageCommands(self._runner)

    @property
    def config(self) -> ConfigCommands:
        """The `ocx config` command group — the managed-config tier."""
        return ConfigCommands(self._runner)

    @property
    def patch(self) -> PatchCommands:
        """The `ocx patch` command group."""
        return PatchCommands(self._runner)

    def version(self, *, timeout: MaybeTimeout = UNSET, retry: MaybeRetry = UNSET) -> str:
        """Return the binary's version, as `ocx version` prints it.

        Reads the plain output — a bare semver line — which is ocx's
        documented stable script contract and the recorded exception to the
        `--format json` pinning rule. This call is the compatibility probe
        itself, so it never triggers the gate and never raises
        `VersionCompatError`: asking an unsupported binary what it is has to
        work. The richer JSON envelope is available through
        `VersionInfo.from_json(ocx.invoke(["--format", "json",
        "version"]).stdout)`.

        Args:
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The version string, whitespace stripped.
        """
        return self._runner.probe(timeout=timeout, retry=retry)

    def about(self, *, timeout: MaybeTimeout = UNSET, retry: MaybeRetry = UNSET) -> AboutInfo:
        """Report what this ocx build is and where it keeps its state.

        Args:
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The parsed `ocx about` payload.
        """
        return AboutInfo.from_json(self._runner.typed(("about",), timeout=timeout, retry=retry).stdout)

    def login(
        self,
        registry: str | None = None,
        *,
        username: str,
        token: str,
        allow_insecure_store: bool = False,
        verify: bool = True,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> LoginResult:
        """Authenticate to a registry and let ocx persist the credentials.

        The token travels on stdin through `--password-stdin`, never in argv,
        and is redacted from logs and errors for the duration of the call.

        Retries are off by default: a timed-out login exits 75, and
        re-sending credentials on a timeout is the auth-retry mistake the
        policy exists to prevent. Pass `retry=` explicitly to override.

        Args:
            registry: The registry hostname. `None` falls back to ocx's
                `OCX_DEFAULT_REGISTRY`.
            username: The account name. Required — the SDK never prompts.
            token: The password or token, written to ocx's stdin.
            allow_insecure_store: Permit the plaintext `auths` fallback when
                no credential helper is configured.
            verify: Check the credentials against the registry before
                storing them.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. Defaults to no retries.

        Returns:
            The registry and username ocx recorded.
        """
        command = [
            "login",
            "--username",
            username,
            "--password-stdin",
            *_switch("--allow-insecure-store", allow_insecure_store),
            *_switch("--no-verify", not verify),
        ]
        result = self._runner.typed(
            command,
            () if registry is None else (registry,),
            input=token,
            timeout=timeout,
            retry=retry,
            mutating=True,
        )
        return LoginResult.from_json(result.stdout)

    def logout(
        self,
        registry: str | None = None,
        *,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> LogoutResult:
        """Drop the stored credentials for a registry.

        Args:
            registry: The registry hostname. `None` falls back to ocx's
                `OCX_DEFAULT_REGISTRY`.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The registry ocx cleared. Clearing an absent entry is not an
            error.
        """
        positionals = () if registry is None else (registry,)
        result = self._runner.typed(("logout",), positionals, timeout=timeout, retry=retry)
        return LogoutResult.from_json(result.stdout)

    def invoke(
        self,
        argv: Sequence[str],
        *,
        check: bool = True,
        capture: bool = True,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> CommandResult:
        """Run an arbitrary ocx command line — the raw escape hatch.

        The configured globals and the composed child environment still
        apply; presentation does not, so ask for `--format json` yourself if
        you intend to parse the output. Commands reached this way are
        possible but unsupported: nothing pins their shape, and the
        compatibility gate does not run.

        Args:
            argv: The command and its arguments, without the binary.
            check: Raise on a non-zero exit instead of returning it.
            capture: Pipe and capture both streams.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The exit code and captured output.

        Raises:
            OcxProcessError: A non-zero exit under `check`.
            OcxTimeoutError: The timeout expired.
        """
        composed = self._runner.compose(argv, presentation=_RAW)
        return self._runner.finish(composed, capture=capture, check=check, timeout=timeout, retry=retry)

    async def invoke_async(
        self,
        argv: Sequence[str],
        *,
        check: bool = True,
        capture: bool = True,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> CommandResult:
        """Run an arbitrary ocx command line on the event loop.

        The async twin of `invoke`. Cancelling the awaiting task terminates
        the child before the `CancelledError` propagates.

        Args:
            argv: The command and its arguments, without the binary.
            check: Raise on a non-zero exit instead of returning it.
            capture: Pipe and capture both streams.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The exit code and captured output.

        Raises:
            ValueError: The handle carries an `on_log` callback, which v0.1
                refuses on async paths.
            OcxProcessError: A non-zero exit under `check`.
            OcxTimeoutError: The timeout expired.
        """
        composed = self._runner.compose(argv, presentation=_RAW)
        return await self._runner.finish_async(composed, capture=capture, check=check, timeout=timeout, retry=retry)

    def spawn(self, argv: Sequence[str], **popen_kw: Any) -> subprocess.Popen[Any]:
        """Start an arbitrary ocx command line and return the live `Popen`.

        No timeout and no pumps: waiting, draining pipes, and killing belong
        to the caller, exactly as with a bare `Popen`.

        Args:
            argv: The command and its arguments, without the binary.
            **popen_kw: Forwarded to `Popen`. `args`, `shell`, and
                `executable` are rejected; the environment is SDK-composed.

        Returns:
            The running child.

        Raises:
            ValueError: `args`, `shell`, or `executable` was passed.
            TypeError: `env` was passed. It is the process layer's own
                parameter, so it arrives as a duplicate argument rather than
                a rejected one. Put what the child needs into `OcxConfig` or
                the `HostEnv` this handle was built with.
        """
        return self._runner.launch(self._runner.compose(argv, presentation=_RAW), popen_kw)

    async def spawn_async(self, argv: Sequence[str], **popen_kw: Any) -> asyncio.subprocess.Process:
        """Start an arbitrary ocx command line on the event loop.

        Args:
            argv: The command and its arguments, without the binary.
            **popen_kw: Forwarded to the subprocess factory. `args`, `shell`,
                and `executable` are rejected.

        Returns:
            The running child.

        Raises:
            ValueError: `args`, `shell`, or `executable` was passed.
            TypeError: `env` was passed — see `spawn`.
        """
        return await self._runner.launch_async(self._runner.compose(argv, presentation=_RAW), popen_kw)


@dataclass(frozen=True, slots=True)
class _Runner:
    """How a handle talks to the binary: argv, environment, and the gate.

    Every public handle — `Ocx`, `Project`, and the command namespaces —
    holds the same runner rather than reaching into each other, so the
    execution machinery has one home and the handles stay thin.

    Attributes:
        exe: The resolved binary, fixed when the `Ocx` was constructed.
        config: The session configuration this runner composes from.
        host: The host snapshot children inherit from.
        on_log: The stderr-line callback, or `None`.
        compat: The version memo, shared with every derived runner.
    """

    exe: Path
    config: OcxConfig
    host: HostEnv
    on_log: Callable[[str], None] | None
    compat: _CompatCell

    def derive(self, **overrides: Unpack[ConfigOverrides]) -> _Runner:
        """Replace configuration fields, keeping the binary and the memo."""
        return _Runner(self.exe, replace(self.config, **overrides), self.host, self.on_log, self.compat)

    def probe(self, *, timeout: MaybeTimeout, retry: MaybeRetry) -> str:
        """Read the plain `ocx version` line, ungated."""
        argv = self.compose(("version",), presentation=_PLAIN)
        return self.finish(argv, timeout=timeout, retry=retry).stdout.strip()

    def spawn_env(self) -> SpawnEnv:
        """Compose the child environment for one call, secrets and all."""
        return build_spawn_env(self.host, self.config)

    def flags(self, presentation: Sequence[str], project: Path | None) -> list[str]:
        """Build the flags that go ahead of the subcommand.

        Only `log_level` and `--project` reach argv: every other `OcxConfig`
        field travels as an `OCX_*` variable composed by `build_spawn_env`,
        which is the single place the config-to-wire mapping lives.
        """
        flags = list(presentation)
        if self.config.log_level is not None:
            flags += ["--log-level", self.config.log_level]
        if project is not None:
            flags += ["--project", str(project)]
        return flags

    def compose(
        self,
        command: Sequence[str],
        positionals: Sequence[str] = (),
        *,
        presentation: Sequence[str] = _JSON,
        project: Path | None = None,
    ) -> tuple[str, ...]:
        """Compose the argv for a one-shot command."""
        return compose_argv(
            str(self.exe),
            self.flags(presentation, project),
            command,
            positionals,
        )

    def compose_child(
        self,
        command: Sequence[str],
        names: Sequence[str],
        child_argv: Sequence[str],
        *,
        project: Path | None = None,
    ) -> tuple[str, ...]:
        """Compose the argv for a command that hosts a child behind `--`.

        Two positional groups: the names ocx resolves, which `compose_argv`
        guards against a leading dash, and the child argv, which it emits
        behind `--` where a leading dash is meaningful and safe.
        """
        if not child_argv:
            raise ValueError(
                "this command needs a command to run: ocx's grammar puts the child argv after `--` and "
                "requires at least one word there. Pass the program and its arguments, as in ['task', 'verify']."
            )
        return compose_argv(str(self.exe), self.flags(_PLAIN, project), command, names, child=child_argv)

    def finish(
        self,
        argv: Sequence[str],
        *,
        capture: bool = True,
        check: bool = True,
        ok_codes: Collection[int] = (0,),
        input: str | None = None,
        timeout: MaybeTimeout,
        retry: MaybeRetry,
        mutating: bool = False,
        cwd: Path | None = None,
    ) -> CommandResult:
        """Run a composed argv and wrap the outcome."""
        env = self.spawn_env()
        done = run_command(
            argv,
            env.mapping,
            input=input,
            redact=env.redact,
            cwd=cwd,
            timeout=self.timeout_for(timeout),
            retry=self.retry_for(retry, mutating=mutating),
            on_log=self.on_log,
            capture=capture,
            check=check,
            ok_codes=ok_codes,
        )
        return _result(argv, done, env.redact)

    async def finish_async(
        self,
        argv: Sequence[str],
        *,
        capture: bool = True,
        check: bool = True,
        timeout: MaybeTimeout,
        retry: MaybeRetry,
        mutating: bool = False,
    ) -> CommandResult:
        """Run a composed argv on the event loop and wrap the outcome."""
        env = self.spawn_env()
        done = await run_command_async(
            argv,
            env.mapping,
            redact=env.redact,
            timeout=self.timeout_for(timeout),
            retry=self.retry_for(retry, mutating=mutating),
            on_log=self.on_log,
            capture=capture,
            check=check,
        )
        return _result(argv, done, env.redact)

    def launch(self, argv: Sequence[str], popen_kw: dict[str, Any]) -> subprocess.Popen[Any]:
        """Start a live child and hand it back untouched."""
        env = self.spawn_env()
        return spawn(argv, env.mapping, redact=env.redact, **popen_kw)

    async def launch_async(self, argv: Sequence[str], popen_kw: dict[str, Any]) -> asyncio.subprocess.Process:
        """Start a live child on the event loop and hand it back untouched."""
        env = self.spawn_env()
        return await spawn_async(argv, env.mapping, redact=env.redact, **popen_kw)

    def typed(
        self,
        command: Sequence[str],
        positionals: Sequence[str] = (),
        *,
        project: Path | None = None,
        input: str | None = None,
        timeout: MaybeTimeout,
        retry: MaybeRetry,
        mutating: bool = False,
        cwd: Path | None = None,
        ok_codes: Collection[int] = (0,),
    ) -> CommandResult:
        """Gate on compatibility, then run a JSON-pinned command."""
        self.gate()
        argv = self.compose(command, positionals, project=project)
        return self.finish(
            argv, input=input, timeout=timeout, retry=retry, mutating=mutating, cwd=cwd, ok_codes=ok_codes
        )

    def retry_for(self, retry: MaybeRetry, *, mutating: bool) -> RetryPolicy | None:
        """Resolve the effective retry policy for one call."""
        if retry is not UNSET:
            return retry
        return None if mutating else self.config.retry

    def timeout_for(self, timeout: MaybeTimeout) -> float | None:
        """Resolve the effective timeout for one call."""
        return self.config.timeout if timeout is UNSET else timeout

    def gate(self) -> None:
        """Probe the binary's version once per handle family."""
        if self.compat.checked:
            return
        self.accept(self.probe(timeout=UNSET, retry=UNSET))

    async def gate_async(self) -> None:
        """Probe the binary's version without blocking the event loop."""
        if self.compat.checked:
            return
        argv = self.compose(("version",), presentation=_PLAIN)
        probed = await self.finish_async(argv, timeout=UNSET, retry=UNSET)
        self.accept(probed.stdout.strip())

    def accept(self, found: str) -> None:
        """Compare a probed version against the supported window.

        A pre-release tail is ignored, so `0.5.8-rc1` reads as `0.5.8` — the
        permissive direction, and cheaper than carrying a semver
        implementation for a comparison the nightly canary re-checks anyway.
        """
        core = _core(found)
        if not core or core < _core(MIN_SUPPORTED):
            raise VersionCompatError(found, MIN_SUPPORTED)
        if core > _core(TESTED_OCX_VERSION):
            _LOG.debug("ocx %s is newer than the tested %s; that is expected.", found, TESTED_OCX_VERSION)
        self.compat.checked = True


@dataclass(frozen=True, slots=True, repr=False)
class Project:
    """A project-tier handle: every call carries `--project <file>`.

    Obtained from `Ocx.project(path)`, never constructed directly. Because
    the path travels explicitly on every call, no method depends on the
    working directory, and an ambient `OCX_PROJECT` can never retarget one.
    `init` is the exception, and only because ocx's is: it takes no flags and
    writes into the working directory, so the handle sets that instead.

    Example:
        ```python
        from ocx_sdk import Ocx

        project = Ocx().project("/srv/build")
        project.add("ocx.sh/go-task/task:3", group="ci")
        project.lock()
        report = project.env()
        environment = report.compose().mapping
        ```

    Attributes:
        path: The absolute project file — the `ocx.toml` itself, which is what
            ocx's `--project` names.
        session_config: The `OcxConfig` every call spawns under.
    """

    _runner: _Runner
    path: Path

    def __repr__(self) -> str:
        return f"Project(path={str(self.path)!r}, exe={str(self._runner.exe)!r})"

    @property
    def session_config(self) -> OcxConfig:
        """The `OcxConfig` this handle spawns under — see `Ocx.session_config`."""
        return self._runner.config

    def with_config(self, **overrides: Unpack[ConfigOverrides]) -> Project:
        """Derive a project handle with some configuration fields replaced.

        Args:
            **overrides: `OcxConfig` field names and their new values, as
                `ConfigOverrides` spells them.

        Returns:
            The same project, seen through a derived `Ocx`.

        Raises:
            TypeError: An override names no `OcxConfig` field.
        """
        return Project(self._runner.derive(**overrides), self.path)

    def init(self, *, timeout: MaybeTimeout = UNSET, retry: MaybeRetry = UNSET) -> CommandResult:
        """Create a minimal `ocx.toml` in the project directory.

        The one project-tier call that carries no `--project`: `ocx init`
        takes no flags and writes into the working directory, and naming a
        project would ask ocx to resolve the very file the call exists to
        create. This runs it with the project's directory as its `cwd`
        instead, so the file lands where the handle points rather than
        wherever the caller happens to be.

        Returns the raw result: ocx pins no JSON payload for `init`, so there
        is nothing to type yet.

        Args:
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The exit code and whatever ocx printed.
        """
        return self._runner.typed(("init",), timeout=timeout, retry=retry, cwd=self.path.parent)

    def add(
        self,
        *refs: PackageLike,
        group: str | None = None,
        pull: bool | None = None,
        platform: str | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> tuple[ToolRow, ...]:
        """Add tool bindings to `ocx.toml`.

        Args:
            *refs: Identifiers, each optionally prefixed `NAME=`.
            group: The group to add to. `None` uses the implicit `[tools]`
                table.
            pull: Materialize the resolved packages. `None` leaves the
                choice to ocx's own default.
            platform: The platform to resolve against, as
                `os/arch[/variant][+feature]`.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The lock rows the call wrote.
        """
        command = [
            "add",
            *_flag("--group", group),
            *_toggle("--pull", "--no-pull", pull),
            *_flag("--platform", platform),
        ]
        return parse_tool_rows(self._call(command, _identifiers(refs), timeout=timeout, retry=retry).stdout)

    def remove(
        self,
        *names: PackageLike,
        group: str | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> tuple[ToolRow, ...]:
        """Remove tool bindings from `ocx.toml`.

        Args:
            *names: Binding names or fully-qualified identifiers.
            group: The group to target. `None` searches every group and
                fails when a name appears in more than one.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The lock rows the call wrote — empty when nothing changed.
        """
        command = ["remove", *_flag("--group", group)]
        return parse_tool_rows(self._call(command, _identifiers(names), timeout=timeout, retry=retry).stdout)

    def lock(
        self,
        *,
        check_only: bool = False,
        pull: bool | None = None,
        platform: str | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> tuple[ToolRow, ...] | None:
        """Resolve declared tags to digests and write `ocx.lock`.

        Args:
            check_only: Verify the lock is current and write nothing — ocx's
                `--check`. Drift raises `DataError`; a missing lock raises
                `ConfigError`. Named apart from the `check=` on `run`, which
                is the `subprocess.run` sense of "raise on a non-zero exit".
            pull: Materialize the resolved packages. `None` leaves the
                choice to ocx's own default.
            platform: The platform to resolve against.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The lock rows written, or `None` under `check_only` — ocx reports
            a current lock with exit 0 and an empty body.
        """
        command = [
            "lock",
            *_switch("--check", check_only),
            *_toggle("--pull", "--no-pull", pull),
            *_flag("--platform", platform),
        ]
        return _rows_or_none(self._call(command, timeout=timeout, retry=retry).stdout)

    def update(
        self,
        *names: str,
        check_only: bool = False,
        groups: Iterable[str] = (),
        pull: bool | None = None,
        platform: str | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> tuple[ToolRow, ...] | None:
        """Re-resolve declared tags against the registry.

        Args:
            *names: Binding names to advance; every other pin freezes.
                Omitted advances the whole file.
            check_only: Compare the candidate lock to its predecessor and
                write nothing — ocx's `--check`. A changed pin raises
                `DataError`.
            groups: Groups to advance. `default` is the implicit `[tools]`
                table, `all` expands to every group.
            pull: Materialize the resolved packages. `None` leaves the
                choice to ocx's own default.
            platform: The platform to resolve against.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The lock rows written, or `None` under `check_only`.
        """
        command = [
            "update",
            *_switch("--check", check_only),
            *_repeated("--group", groups),
            *_toggle("--pull", "--no-pull", pull),
            *_flag("--platform", platform),
        ]
        return _rows_or_none(self._call(command, tuple(names), timeout=timeout, retry=retry).stdout)

    def pull(
        self,
        *,
        dry_run: bool = False,
        groups: Iterable[str] = (),
        platform: str | None = None,
        lazy_mode: LazyMode | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> PullReport:
        """Pre-warm the object store from `ocx.lock`.

        Args:
            dry_run: Report what would be fetched without writing.
            groups: Groups to restrict the pull to.
            platform: The platform to resolve against.
            lazy_mode: When content downloads — now, or on first use.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The materialized paths and any advisories.
        """
        command = [
            "pull",
            *_switch("--dry-run", dry_run),
            *_repeated("--group", groups),
            *_flag("--platform", platform),
            *_flag("--lazy-mode", lazy_mode),
        ]
        return PullReport.from_json(self._call(command, timeout=timeout, retry=retry).stdout)

    def status(self, *, timeout: MaybeTimeout = UNSET, retry: MaybeRetry = UNSET) -> StatusReport:
        """Report what `ocx.toml` and `ocx.lock` declare, resolving nothing.

        A broken or stale lock is payload here, not failure: the call still
        exits 0 and the state shows up on the report.

        Args:
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The declared toolchain and the lock's state.
        """
        return StatusReport.from_json(self._call(("status",), timeout=timeout, retry=retry).stdout)

    def inspect(
        self,
        *names: str,
        groups: Iterable[str] = (),
        platform: str | None = None,
        env: Mapping[str, EnvValue] | None = None,
        resolve: bool = False,
        closure: bool = False,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> InspectReport:
        """Inspect the toolchain's bindings and their resolution.

        Args:
            *names: Binding names to inspect. Omitted inspects everything.
            groups: Groups to restrict to.
            platform: The platform to resolve against.
            env: Extra `[env]` entries for this call, as
                `KEY[:TYPE[:SEP]]=VALUE` flags.
            resolve: Add the pinned identifier, digest, layers, and the
                resolution chain.
            closure: Add the dependency closure, its surface, and any
                conflicts. Conflicting entrypoints raise `DataError` with
                the payload attached to the error's stderr.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The inspected packages and the composed `[env]` entries.
        """
        command = [
            "inspect",
            *_repeated("--group", groups),
            *_flag("--platform", platform),
            *_switch("--resolve", resolve),
            *_switch("--closure", closure),
            *_env_flags(env),
        ]
        return InspectReport.from_json(self._call(command, tuple(names), timeout=timeout, retry=retry).stdout)

    def env(
        self,
        *,
        groups: Iterable[str] = (),
        platform: str | None = None,
        env: Mapping[str, EnvValue] | None = None,
        pull: bool | None = None,
        lazy_mode: LazyMode | None = None,
        show_patches: bool = False,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> EnvReport:
        """Report the environment the toolchain composes.

        The report carries this handle's host snapshot, so `compose()` stays
        hermetic: a report produced under `HostEnv.clean()` composes against
        nothing ambient unless you pass a different base.

        Note that ocx's `env` command takes no binding names — narrow with
        `groups` instead.

        Args:
            groups: Groups to compose. Omitted composes the default set.
            platform: The platform to resolve against.
            env: Extra `[env]` entries for this call.
            pull: Materialize missing content first. `None` leaves the
                choice to ocx's own default.
            lazy_mode: When content downloads — now, or on first use.
            show_patches: Include patch-contributed entries.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The typed entries plus the binaries, entrypoints, integrations,
            and advisories envelope.
        """
        command = [
            "env",
            *_repeated("--group", groups),
            *_flag("--platform", platform),
            *_toggle("--pull", "--no-pull", pull),
            *_flag("--lazy-mode", lazy_mode),
            *_switch("--show-patches", show_patches),
            *_env_flags(env),
        ]
        raw = self._call(command, timeout=timeout, retry=retry).stdout
        return EnvReport.from_json(raw, base=self._runner.host.source)

    def run(
        self,
        argv: Sequence[str],
        *,
        names: Iterable[str] = (),
        groups: Iterable[str] = (),
        clean: bool = False,
        env: Mapping[str, EnvValue] | None = None,
        lazy_mode: LazyMode | None = None,
        capture: bool = True,
        check: bool = True,
        timeout: MaybeTimeout = UNSET,
    ) -> CommandResult:
        """Run a command inside the project's composed environment.

        The child's exit code comes back byte for byte, so `check=False` is
        how you inspect a failing build instead of catching an exception.
        Child processes are never retried.

        Args:
            argv: The command and its arguments. Must not be empty.
            names: Bindings to compose. Omitted composes the default set.
            groups: Groups to compose.
            clean: Strip the ambient parent environment before composing.
            env: Extra `[env]` entries for this call.
            lazy_mode: When content downloads — now, or on first use.
            capture: Pipe and capture both streams. `False` inherits stdio
                and forwards SIGINT to the child.
            check: Raise on a non-zero child exit instead of returning it.
            timeout: Seconds for the whole run. Omitted takes the config's.

        Returns:
            The child's exit code and, under `capture`, its output.

        Raises:
            ValueError: `argv` is empty — ocx requires a command after `--`.
            OcxProcessError: The child exited non-zero under `check`.
            OcxTimeoutError: The timeout expired.
        """
        composed = self._child_argv(argv, names, groups, clean, env, lazy_mode)
        self._runner.gate()
        return self._runner.finish(composed, capture=capture, check=check, timeout=timeout, retry=None)

    async def run_async(
        self,
        argv: Sequence[str],
        *,
        names: Iterable[str] = (),
        groups: Iterable[str] = (),
        clean: bool = False,
        env: Mapping[str, EnvValue] | None = None,
        lazy_mode: LazyMode | None = None,
        capture: bool = True,
        check: bool = True,
        timeout: MaybeTimeout = UNSET,
    ) -> CommandResult:
        """Run a command in the project's environment, on the event loop.

        Args:
            argv: The command and its arguments. Must not be empty.
            names: Bindings to compose.
            groups: Groups to compose.
            clean: Strip the ambient parent environment before composing.
            env: Extra `[env]` entries for this call.
            lazy_mode: When content downloads — now, or on first use.
            capture: Pipe and capture both streams.
            check: Raise on a non-zero child exit instead of returning it.
            timeout: Seconds for the whole run. Omitted takes the config's.

        Returns:
            The child's exit code and, under `capture`, its output.

        Raises:
            ValueError: `argv` is empty, or the handle carries an `on_log`
                callback, which v0.1 refuses on async paths.
            OcxProcessError: The child exited non-zero under `check`.
            OcxTimeoutError: The timeout expired.
        """
        composed = self._child_argv(argv, names, groups, clean, env, lazy_mode)
        await self._runner.gate_async()
        return await self._runner.finish_async(composed, capture=capture, check=check, timeout=timeout, retry=None)

    def spawn(
        self,
        argv: Sequence[str],
        *,
        names: Iterable[str] = (),
        groups: Iterable[str] = (),
        clean: bool = False,
        env: Mapping[str, EnvValue] | None = None,
        lazy_mode: LazyMode | None = None,
        **popen_kw: Any,
    ) -> subprocess.Popen[Any]:
        """Start a command in the project's environment and return `Popen`.

        Args:
            argv: The command and its arguments. Must not be empty.
            names: Bindings to compose.
            groups: Groups to compose.
            clean: Strip the ambient parent environment before composing.
            env: Extra `[env]` entries for this call, not the child's
                environment — that one is SDK-composed.
            lazy_mode: When content downloads — now, or on first use.
            **popen_kw: Forwarded to `Popen`. `args`, `shell`, and
                `executable` are rejected.

        Returns:
            The running child.

        Raises:
            ValueError: `argv` is empty, or a rejected keyword was passed.
        """
        composed = self._child_argv(argv, names, groups, clean, env, lazy_mode)
        self._runner.gate()
        return self._runner.launch(composed, popen_kw)

    async def spawn_async(
        self,
        argv: Sequence[str],
        *,
        names: Iterable[str] = (),
        groups: Iterable[str] = (),
        clean: bool = False,
        env: Mapping[str, EnvValue] | None = None,
        lazy_mode: LazyMode | None = None,
        **popen_kw: Any,
    ) -> asyncio.subprocess.Process:
        """Start a command in the project's environment, on the event loop.

        Args:
            argv: The command and its arguments. Must not be empty.
            names: Bindings to compose.
            groups: Groups to compose.
            clean: Strip the ambient parent environment before composing.
            env: Extra `[env]` entries for this call.
            lazy_mode: When content downloads — now, or on first use.
            **popen_kw: Forwarded to the subprocess factory. `args`,
                `shell`, and `executable` are rejected.

        Returns:
            The running child.

        Raises:
            ValueError: `argv` is empty, or a rejected keyword was passed.
        """
        composed = self._child_argv(argv, names, groups, clean, env, lazy_mode)
        await self._runner.gate_async()
        return await self._runner.launch_async(composed, popen_kw)

    def _call(
        self,
        command: Sequence[str],
        positionals: Sequence[str] = (),
        *,
        timeout: MaybeTimeout,
        retry: MaybeRetry,
    ) -> CommandResult:
        """Run a typed project-tier command, injecting `--project`."""
        return self._runner.typed(command, positionals, project=self.path, timeout=timeout, retry=retry)

    def _child_argv(
        self,
        argv: Sequence[str],
        names: Iterable[str],
        groups: Iterable[str],
        clean: bool,
        env: Mapping[str, EnvValue] | None,
        lazy_mode: LazyMode | None,
    ) -> tuple[str, ...]:
        """Compose `ocx run --project … [NAMES] -- ARGV`."""
        command = [
            "run",
            *_repeated("--group", groups),
            *_switch("--clean", clean),
            *_flag("--lazy-mode", lazy_mode),
            *_env_flags(env),
        ]
        return self._runner.compose_child(command, tuple(names), tuple(argv), project=self.path)


@dataclass(frozen=True, slots=True, repr=False)
class PackageCommands:
    """The `ocx package` command group — machine tier.

    Package operations act on the `$OCX_HOME` store and its candidate and
    current symlinks. They take no project path and are CWD-independent by
    construction; a path appears only where the CLI itself takes one.

    Every method is multi-identifier native, mirroring the CLI's `PKG...`
    with one shared resolution.
    """

    _runner: _Runner

    def __repr__(self) -> str:
        return f"PackageCommands(exe={str(self._runner.exe)!r})"

    def install(
        self,
        *refs: PackageLike,
        platform: str | None = None,
        select: bool = False,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> InstallReport:
        """Install packages into the store.

        Args:
            *refs: Package identifiers.
            platform: The platform to resolve against.
            select: Also make each installed version current.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The installed packages, keyed by the identifier as given.
        """
        command = ["package", "install", *_switch("--select", select), *_flag("--platform", platform)]
        return InstallReport.from_json(self._call(command, _identifiers(refs), timeout=timeout, retry=retry).stdout)

    def select(
        self,
        *refs: PackageLike,
        platform: str | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> InstallReport:
        """Make an installed version the current one.

        Args:
            *refs: Package identifiers.
            platform: The platform to resolve against.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The selected packages, whose `path` names the `current` symlink.
        """
        command = ["package", "select", *_flag("--platform", platform)]
        return InstallReport.from_json(self._call(command, _identifiers(refs), timeout=timeout, retry=retry).stdout)

    def uninstall(
        self,
        *refs: PackageLike,
        deselect: bool = False,
        purge: bool = False,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> tuple[RemovalResult, ...]:
        """Remove installed candidates.

        Args:
            *refs: Package identifiers.
            deselect: Also drop the `current` symlink.
            purge: Delete the object from the store once nothing references
                it.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            One row per removal.
        """
        command = ["package", "uninstall", *_switch("--deselect", deselect), *_switch("--purge", purge)]
        return parse_removals(self._call(command, _identifiers(refs), timeout=timeout, retry=retry).stdout)

    def deselect(
        self,
        *refs: PackageLike,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> tuple[RemovalResult, ...]:
        """Drop the `current` symlink, leaving the candidates installed.

        Args:
            *refs: Package identifiers.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            One row per removal.
        """
        command = ["package", "deselect"]
        return parse_removals(self._call(command, _identifiers(refs), timeout=timeout, retry=retry).stdout)

    def env(
        self,
        *refs: PackageLike,
        platform: str | None = None,
        env: Mapping[str, EnvValue] | None = None,
        resolve: Resolve | None = None,
        private: bool = False,
        lazy_mode: LazyMode | None = None,
        show_patches: bool = False,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> EnvReport:
        """Report the environment a set of packages composes.

        Returns the same envelope as the project-tier `env`, and carries the
        same host snapshot for a hermetic `compose()`.

        Args:
            *refs: Package identifiers.
            platform: The platform to resolve against.
            env: Extra `[env]` entries for this call.
            resolve: Which store symlink to resolve through — `'candidate'`
                or `'current'`. `None` leaves the choice to ocx. One value,
                not two booleans: ocx's `--candidate` and `--current` name
                one link each, so a pair of flags could ask for both.
            private: Compose the private surface instead of the interface
                one — ocx's `--self`.
            lazy_mode: When content downloads — now, or on first use.
            show_patches: Include patch-contributed entries.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The typed entries plus the full envelope.
        """
        command = [
            "package",
            "env",
            *_resolve_flag(resolve),
            *_switch("--self", private),
            *_flag("--platform", platform),
            *_flag("--lazy-mode", lazy_mode),
            *_switch("--show-patches", show_patches),
            *_env_flags(env),
        ]
        raw = self._call(command, _identifiers(refs), timeout=timeout, retry=retry).stdout
        return EnvReport.from_json(raw, base=self._runner.host.source)

    def exec(
        self,
        refs: Sequence[PackageLike],
        argv: Sequence[str],
        *,
        platform: str | None = None,
        clean: bool = False,
        private: bool = False,
        env: Mapping[str, EnvValue] | None = None,
        lazy_mode: LazyMode | None = None,
        capture: bool = True,
        check: bool = True,
        timeout: MaybeTimeout = UNSET,
    ) -> CommandResult:
        """Run a command in a set of packages' composed environment.

        Two positional groups, mirroring the CLI's `PKG... -- CMD...`. The
        child's exit code comes back byte for byte; child processes are
        never retried.

        Args:
            refs: Package identifiers to compose.
            argv: The command and its arguments. Must not be empty.
            platform: The platform to resolve against.
            clean: Strip the ambient parent environment before composing.
            private: Compose the private surface — ocx's `--self`.
            env: Extra `[env]` entries for this call.
            lazy_mode: When content downloads — now, or on first use.
            capture: Pipe and capture both streams.
            check: Raise on a non-zero child exit instead of returning it.
            timeout: Seconds for the whole run. Omitted takes the config's.

        Returns:
            The child's exit code and, under `capture`, its output.

        Raises:
            ValueError: `argv` is empty.
            OcxProcessError: The child exited non-zero under `check`.
            OcxTimeoutError: The timeout expired.
        """
        composed = self._exec_argv(refs, argv, platform, clean, private, env, lazy_mode)
        self._runner.gate()
        return self._runner.finish(composed, capture=capture, check=check, timeout=timeout, retry=None)

    async def exec_async(
        self,
        refs: Sequence[PackageLike],
        argv: Sequence[str],
        *,
        platform: str | None = None,
        clean: bool = False,
        private: bool = False,
        env: Mapping[str, EnvValue] | None = None,
        lazy_mode: LazyMode | None = None,
        capture: bool = True,
        check: bool = True,
        timeout: MaybeTimeout = UNSET,
    ) -> CommandResult:
        """Run a command in a packages' environment, on the event loop.

        Args:
            refs: Package identifiers to compose.
            argv: The command and its arguments. Must not be empty.
            platform: The platform to resolve against.
            clean: Strip the ambient parent environment before composing.
            private: Compose the private surface — ocx's `--self`.
            env: Extra `[env]` entries for this call.
            lazy_mode: When content downloads — now, or on first use.
            capture: Pipe and capture both streams.
            check: Raise on a non-zero child exit instead of returning it.
            timeout: Seconds for the whole run. Omitted takes the config's.

        Returns:
            The child's exit code and, under `capture`, its output.

        Raises:
            ValueError: `argv` is empty, or the handle carries an `on_log`
                callback, which v0.1 refuses on async paths.
            OcxProcessError: The child exited non-zero under `check`.
            OcxTimeoutError: The timeout expired.
        """
        composed = self._exec_argv(refs, argv, platform, clean, private, env, lazy_mode)
        await self._runner.gate_async()
        return await self._runner.finish_async(composed, capture=capture, check=check, timeout=timeout, retry=None)

    def spawn(
        self,
        refs: Sequence[PackageLike],
        argv: Sequence[str],
        *,
        platform: str | None = None,
        clean: bool = False,
        private: bool = False,
        env: Mapping[str, EnvValue] | None = None,
        lazy_mode: LazyMode | None = None,
        **popen_kw: Any,
    ) -> subprocess.Popen[Any]:
        """Start a command in a packages' environment and return `Popen`.

        Args:
            refs: Package identifiers to compose.
            argv: The command and its arguments. Must not be empty.
            platform: The platform to resolve against.
            clean: Strip the ambient parent environment before composing.
            private: Compose the private surface — ocx's `--self`.
            env: Extra `[env]` entries for this call.
            lazy_mode: When content downloads — now, or on first use.
            **popen_kw: Forwarded to `Popen`. `args`, `shell`, and
                `executable` are rejected.

        Returns:
            The running child.

        Raises:
            ValueError: `argv` is empty, or a rejected keyword was passed.
        """
        composed = self._exec_argv(refs, argv, platform, clean, private, env, lazy_mode)
        self._runner.gate()
        return self._runner.launch(composed, popen_kw)

    async def spawn_async(
        self,
        refs: Sequence[PackageLike],
        argv: Sequence[str],
        *,
        platform: str | None = None,
        clean: bool = False,
        private: bool = False,
        env: Mapping[str, EnvValue] | None = None,
        lazy_mode: LazyMode | None = None,
        **popen_kw: Any,
    ) -> asyncio.subprocess.Process:
        """Start a command in a packages' environment, on the event loop.

        Args:
            refs: Package identifiers to compose.
            argv: The command and its arguments. Must not be empty.
            platform: The platform to resolve against.
            clean: Strip the ambient parent environment before composing.
            private: Compose the private surface — ocx's `--self`.
            env: Extra `[env]` entries for this call.
            lazy_mode: When content downloads — now, or on first use.
            **popen_kw: Forwarded to the subprocess factory. `args`,
                `shell`, and `executable` are rejected.

        Returns:
            The running child.

        Raises:
            ValueError: `argv` is empty, or a rejected keyword was passed.
        """
        composed = self._exec_argv(refs, argv, platform, clean, private, env, lazy_mode)
        await self._runner.gate_async()
        return await self._runner.launch_async(composed, popen_kw)

    def which(
        self,
        *refs: PackageLike,
        platform: str | None = None,
        resolve: Resolve | None = None,
        lazy_mode: LazyMode | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> Mapping[str, WhichResult]:
        """Resolve installed packages to their paths, downloading nothing.

        Args:
            *refs: Package identifiers.
            platform: The platform to resolve against.
            resolve: Which store symlink to resolve through — `'candidate'`
                or `'current'`. `None` leaves the choice to ocx.
            lazy_mode: When content downloads — now, or on first use.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            A path and kind per identifier. This payload is doc-flagged
            pre-1.0 upstream and may change shape.
        """
        command = [
            "package",
            "which",
            *_resolve_flag(resolve),
            *_flag("--platform", platform),
            *_flag("--lazy-mode", lazy_mode),
        ]
        return parse_which(self._call(command, _identifiers(refs), timeout=timeout, retry=retry).stdout)

    def inspect(
        self,
        *refs: PackageLike,
        platform: str | None = None,
        env: Mapping[str, EnvValue] | None = None,
        resolve: bool = False,
        closure: bool = False,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> InspectReport:
        """Inspect packages straight from the registry or the store.

        Args:
            *refs: Package identifiers.
            platform: The platform to resolve against.
            env: Extra `[env]` entries for this call.
            resolve: Add the pinned identifier, digest, layers, and the
                resolution chain, which at this tier starts with an index
                hop the project tier skips.
            closure: Add the dependency closure, its surface, and any
                conflicts.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The inspected packages, whose `name` is the full requested
            identifier at this tier.
        """
        command = [
            "package",
            "inspect",
            *_flag("--platform", platform),
            *_switch("--resolve", resolve),
            *_switch("--closure", closure),
            *_env_flags(env),
        ]
        return InspectReport.from_json(self._call(command, _identifiers(refs), timeout=timeout, retry=retry).stdout)

    def info(
        self,
        *refs: PackageLike,
        save_readme: str | Path | None = None,
        save_logo: str | Path | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> InfoResult:
        """Show the description metadata a registry holds for packages.

        Args:
            *refs: Package repositories to query.
            save_readme: Write the README to this file or directory. ocx
                accepts it for a single package only.
            save_logo: Write the logo to this file or directory. Single
                package only, as above.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            One entry per identifier as given, `None` where the registry
            holds no description metadata.

        Raises:
            ValueError: A save target was given for anything other than
                exactly one package — ocx would refuse it, and refusing here
                names which argument to drop.
        """
        if (save_readme is not None or save_logo is not None) and len(refs) != 1:
            raise ValueError(
                f"save_readme and save_logo write one file, so ocx takes them for a single package only; "
                f"{len(refs)} were given. Call info() once per package, or drop the save target."
            )
        command = ["package", "info", *_flag("--save-readme", save_readme), *_flag("--save-logo", save_logo)]
        return parse_info(self._call(command, _identifiers(refs), timeout=timeout, retry=retry).stdout)

    def deps(
        self,
        *refs: PackageLike,
        platform: str | None = None,
        private: bool = False,
        why: PackageLike | None = None,
        depth: int | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> DepsReport:
        """Show the dependency tree of installed packages.

        Args:
            *refs: Package identifiers.
            platform: The platform to resolve against.
            private: Include the private, self-only edges — ocx's `--self`.
                Generated launchers pass it; a consumer needs it only when
                building a launcher equivalent.
            why: Explain why this dependency is pulled in. Matched by
                registry and repository; the tag is ignored.
            depth: Limit the tree depth. `None` is unlimited.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            One root per requested package.
        """
        # ponytail: --flat is not wrapped — it replaces the tree with a flat
        # evaluation order, so it needs a result-type decision `DepsReport`
        # cannot absorb. invoke() reaches it meanwhile.
        command = [
            "package",
            "deps",
            *_switch("--self", private),
            *_flag("--why", why),
            *_flag("--depth", depth),
            *_flag("--platform", platform),
        ]
        return DepsReport.from_json(self._call(command, _identifiers(refs), timeout=timeout, retry=retry).stdout)

    def pull(
        self,
        *refs: PackageLike,
        platform: str | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> Mapping[str, str]:
        """Download packages into the store without creating symlinks.

        Args:
            *refs: Package identifiers.
            platform: The platform to resolve against.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            A store path per identifier — a bare string here, unlike the
            project tier's `pull` and unlike `which`.
        """
        command = ["package", "pull", *_flag("--platform", platform)]
        return parse_package_pull(self._call(command, _identifiers(refs), timeout=timeout, retry=retry).stdout)

    def create(
        self,
        path: str | Path,
        *,
        identifier: str | None = None,
        platform: str | None = None,
        output: str | Path | None = None,
        metadata: str | Path | None = None,
        force: bool = False,
        compression_level: Literal["fast", "best", "default"] | None = None,
        threads: int | None = None,
        bin_scan: bool | None = None,
        libc_lint: bool = True,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> None:
        """Bundle a local directory into a package archive.

        Writes the bundle, a compiled metadata sidecar when `metadata` is
        given, and a build receipt that `push` and `test` read back. There is
        nothing to return: ocx prints no payload for this command.

        Args:
            path: The package directory to bundle.
            identifier: The identifier the bundle will be published under.
                Recorded in the receipt, where `push` finds it.
            platform: The platform the content runs on. Required whenever
                `metadata` is given.
            output: The output file or directory.
            metadata: A `metadata.json` to validate, resolve, and write
                beside the bundle.
            force: Overwrite an existing output file.
            compression_level: How hard to compress the bundle. `None`
                leaves ocx's own default.
            threads: Compression threads. `0` auto-detects, `1` is
                single-threaded; `None` leaves ocx's own default.
            bin_scan: Scan the content tree for the executables the package
                puts on `PATH`, verifying a declared `binaries` claim or
                filling an absent one. Needs `metadata`. `None` leaves ocx's
                own default, which fills an absent claim.
            libc_lint: Check the packaged binaries against the platform's
                declared `os.features`. `False` is an escape hatch, not a
                convenience: it publishes a package that may not run on
                hosts the declaration claims.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.
        """
        command = [
            "package",
            "create",
            *_flag("--identifier", identifier),
            *_flag("--platform", platform),
            *_flag("--output", output),
            *_flag("--metadata", metadata),
            *_switch("--force", force),
            *_flag("--compression-level", compression_level),
            *_flag("--threads", threads),
            *_toggle("--bin-scan", "--no-bin-scan", bin_scan),
            *_switch("--no-libc-lint", not libc_lint),
        ]
        self._call(command, (str(path),), timeout=timeout, retry=retry)

    def test(
        self,
        identifier: str,
        *,
        script: str | Path,
        layers: Iterable[str | Path] = (),
        platform: str | None = None,
        metadata: str | Path | None = None,
        output: str | Path | None = None,
        keep: bool = False,
        clean: bool = False,
        private: bool = False,
        env: Mapping[str, EnvValue] | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> TestResult:
        """Materialize a package locally and run a Starlark test script.

        Only the `--script` form is typed. ocx's trailing `-- CMD` form
        prints the child's stdout verbatim even under `--format json`, so it
        produces nothing parseable — reach for `invoke` if you need it.

        Args:
            identifier: The identifier to materialize under. Tag form only.
            script: Path to the Starlark test script, or `-` to read the
                script source from stdin.
            layers: Layer archives or digest references, base first.
            platform: The platform to resolve against. Omitted reads the
                build receipt beside the bundle.
            metadata: The compiled metadata sidecar. Required when no file
                layers are given.
            output: Where to materialize the package. `None` uses ocx's
                temp root under `$OCX_HOME`.
            keep: Preserve the temporary build directory.
            clean: Strip the ambient parent environment before composing.
            private: Compose the private surface — ocx's `--self`.
            env: Extra `[env]` entries for this call.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The outcome: `status` decides, and `assertion.kind` is the
            stable machine-readable reason. A failing script is a RESULT
            (exit 1 still carries the envelope), not an exception — only
            resolution or usage failures raise.
        """
        command = [
            "package",
            "test",
            "--identifier",
            identifier,
            "--script",
            str(script),
            *_flag("--platform", platform),
            *_flag("--metadata", metadata),
            *_flag("--output", output),
            *_switch("--keep", keep),
            *_switch("--clean", clean),
            *_switch("--self", private),
            *_env_flags(env),
        ]
        positionals = tuple(str(layer) for layer in layers)
        # Exit 1 is a failed assertion WITH the v1 envelope on stdout — a
        # result to parse, not a process failure (WP13 finding).
        result = self._call(command, positionals, timeout=timeout, retry=retry, ok_codes=(0, 1))
        return TestResult.from_json(result.stdout)

    def push(
        self,
        *layers: str | Path,
        identifier: str | None = None,
        platform: str | None = None,
        metadata: str | Path | None = None,
        new: bool = False,
        cascade: bool = False,
        canonical_tag: bool | None = None,
        build_timestamp: Literal["datetime", "date", "none"] | None = None,
        annotations: Mapping[str, str] | None = None,
        announce_file: str | Path | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> PushResult:
        """Publish a package's layers and metadata to a registry.

        Retries are off by default: a push is a registry write, and
        re-sending one on a timeout can publish twice. Pass `retry=`
        explicitly when the target is known to be idempotent-safe.

        Args:
            *layers: Layer archives or `sha256:<hex>.<ext>` references, base
                first, each optionally carrying a `:strip=,prefix=,from=`
                tail.
            identifier: The identifier to publish under. Omitted reads the
                build receipt beside the bundle.
            platform: The platform the manifest is scoped to. Omitted reads
                the receipt.
            metadata: The compiled metadata sidecar. Required when no file
                layers are given.
            new: Skip the checks that need an existing index.
            cascade: Also advance the rolling tags above this version.
            canonical_tag: Write the `sha256.<hex>` tag. `None` leaves ocx's
                default, which writes it.
            build_timestamp: Append a UTC build-metadata segment to the
                published tag, for rolling continuous-deploy versions. The
                version core in `identifier` must already be `X.Y.Z`.
            annotations: OCI annotations for the published index.
            announce_file: Append the pushed tag and any cascade tags to
                this file, where `ocx package announce --tags-from-file`
                picks them up. A scratch file for one pipeline run, not a
                persistent list.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. Defaults to no retries.

        Returns:
            The published identifier, digest, and tags.
        """
        command = [
            "package",
            "push",
            *_flag("--identifier", identifier),
            *_switch("--cascade", cascade),
            *_switch("--new", new),
            *_toggle("--canonical-tag", "--no-canonical-tag", canonical_tag),
            # The equals form is mandatory: bare `--build-timestamp` means
            # `datetime`, and a space-separated value is a usage error.
            *([] if build_timestamp is None else [f"--build-timestamp={build_timestamp}"]),
            *_flag("--metadata", metadata),
            *_repeated("--annotation", (f"{key}={value}" for key, value in (annotations or {}).items())),
            *_flag("--announce-file", announce_file),
            *_flag("--platform", platform),
        ]
        positionals = tuple(str(layer) for layer in layers)
        result = self._call(command, positionals, timeout=timeout, retry=retry, mutating=True)
        return PushResult.from_json(result.stdout)

    def _call(
        self,
        command: Sequence[str],
        positionals: Sequence[str] = (),
        *,
        timeout: MaybeTimeout,
        retry: MaybeRetry,
        mutating: bool = False,
        ok_codes: Collection[int] = (0,),
    ) -> CommandResult:
        """Run a typed package-tier command — machine tier, no project."""
        return self._runner.typed(
            command, positionals, timeout=timeout, retry=retry, mutating=mutating, ok_codes=ok_codes
        )

    def _exec_argv(
        self,
        refs: Sequence[PackageLike],
        argv: Sequence[str],
        platform: str | None,
        clean: bool,
        private: bool,
        env: Mapping[str, EnvValue] | None,
        lazy_mode: LazyMode | None,
    ) -> tuple[str, ...]:
        """Compose `ocx package exec PKG... -- CMD...`."""
        command = [
            "package",
            "exec",
            *_switch("--clean", clean),
            *_switch("--self", private),
            *_flag("--platform", platform),
            *_flag("--lazy-mode", lazy_mode),
            *_env_flags(env),
        ]
        return self._runner.compose_child(command, _identifiers(refs), tuple(argv))


@dataclass(frozen=True, slots=True, repr=False)
class ConfigCommands:
    """The `ocx config` command group — the corporate managed-config tier."""

    _runner: _Runner

    def __repr__(self) -> str:
        return f"ConfigCommands(exe={str(self._runner.exe)!r})"

    def setup(
        self,
        *,
        managed_config: str | None = None,
        dry_run: bool = False,
        force: bool = False,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> ConfigSetupReport:
        """Adopt, or clear, the managed-config tier.

        Installs no binary and touches no shell profile — the
        automation-shaped counterpart to `self setup --managed-config`.

        Args:
            managed_config: An OCI reference to a managed-config artifact.
                `MANAGED_CONFIG_DISABLED` clears an existing seed. `None`
                falls back to the ambient source, then the existing seed.
            dry_run: Report the intended actions without writing.
            force: Overwrite a managed fence that carries local edits.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The status ocx recorded.

        Raises:
            DirtyRcBlockError: The fence carries local edits and `force` is
                unset.
        """
        command = [
            "config",
            "setup",
            *_flag("--managed-config", managed_config),
            *_switch("--dry-run", dry_run),
            *_switch("--force", force),
        ]
        result = self._runner.typed(command, timeout=timeout, retry=retry)
        return ConfigSetupReport.from_json(result.stdout)

    def update(
        self,
        version: str | None = None,
        *,
        check_only: bool = False,
        pause: str | None = None,
        resume: bool = False,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> ConfigUpdateReport:
        """Refresh the managed-config snapshot from the registry.

        Unlike the project-tier `lock --check`, this command answers `--check`
        with a full payload — `status` reads `checked` or `check_unavailable`.

        Args:
            version: A tag, `sha256:<hex>`, or `tag@sha256:<hex>` to sync.
                `None` follows the seed's own source.
            check_only: Report status — drift, pause, pin — without fetching
                or swapping. ocx's `--check`.
            pause: Hold the background refresh for a duration such as `4h`
                or `3d`, up to seven days.
            resume: Clear an active pause and refresh immediately.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The tier's status, source, digest, and policy.
        """
        command = [
            "config",
            "update",
            *_switch("--check", check_only),
            *_flag("--pause", pause),
            *_switch("--resume", resume),
        ]
        positionals = () if version is None else (version,)
        result = self._runner.typed(command, positionals, timeout=timeout, retry=retry)
        return ConfigUpdateReport.from_json(result.stdout)


@dataclass(frozen=True, slots=True, repr=False)
class PatchCommands:
    """The `ocx patch` command group."""

    _runner: _Runner

    def __repr__(self) -> str:
        return f"PatchCommands(exe={str(self._runner.exe)!r})"

    def sync(
        self,
        *,
        platform: str | None = None,
        timeout: MaybeTimeout = UNSET,
        retry: MaybeRetry = UNSET,
    ) -> CommandResult:
        """Refresh patch descriptors and companion packages.

        Re-checks every installed package, including ones installed before
        patches were configured, and needs network access. Returns the raw
        result: no payload shape is pinned for this command yet.

        Args:
            platform: The platform to resolve against.
            timeout: Seconds per attempt. Omitted takes the config's.
            retry: Retry policy. `None` opts out; omitted takes the config's.

        Returns:
            The exit code and whatever ocx printed.
        """
        command = ["patch", "sync", *_flag("--platform", platform)]
        return self._runner.typed(command, timeout=timeout, retry=retry)


def _flag(name: str, value: object | None) -> list[str]:
    """Render an optional valued flag, or nothing when it is unset.

    An empty string is a value, not an absence: `--managed-config ""` is how
    a caller clears the managed tier.
    """
    return [] if value is None else [name, str(value)]


def _switch(name: str, on: bool) -> list[str]:
    """Render a boolean flag, or nothing when it is off."""
    return [name] if on else []


def _toggle(on_name: str, off_name: str, value: bool | None) -> list[str]:
    """Render one side of a `--x`/`--no-x` pair, or nothing when unset.

    `None` is the third state ocx's own defaults occupy: neither flag is
    emitted and the binary decides.
    """
    if value is None:
        return []
    return [on_name] if value else [off_name]


def _repeated(name: str, values: Iterable[str]) -> list[str]:
    """Render a repeatable flag once per value."""
    return [item for value in values for item in (name, value)]


def _resolve_flag(resolve: Resolve | None) -> list[str]:
    """Render `--candidate`/`--current`, or nothing when ocx should decide."""
    return [] if resolve is None else [f"--{resolve}"]


def _env_flags(env: Mapping[str, EnvValue] | None) -> list[str]:
    """Render `[env]` entries as repeated `--env KEY[:TYPE[:SEP]]=VALUE`."""
    if not env:
        return []
    return _repeated("--env", [serialize_env_value(key, value) for key, value in env.items()])


def _identifiers(refs: Iterable[PackageLike]) -> tuple[str, ...]:
    """Coerce package references to the argv strings ocx reads.

    `str(PackageRef)` round-trips the identifier byte for byte, so a result
    row flows straight back into the next call's argv.
    """
    return tuple(str(ref) for ref in refs)


def _result(argv: Sequence[str], done: Completed, redact: Callable[[str], str]) -> CommandResult:
    """Wrap a finished process, scrubbing secrets from the argv it reports."""
    return CommandResult(
        argv=tuple(redact(arg) for arg in argv),
        exit_code=done.exit_code,
        stdout=done.stdout,
        stderr=done.stderr,
    )


def _rows_or_none(raw: str) -> tuple[ToolRow, ...] | None:
    """Parse lock rows, reading an empty body as "nothing to report".

    `lock --check` and `update --check` answer a current lock with exit 0 and
    no payload at all, even under `--format json` (WP00 addendum).
    """
    return parse_tool_rows(raw) if raw.strip() else None


def _core(version: str) -> tuple[int, ...]:
    """Extract a version's dotted numeric head for comparison.

    Returns an empty tuple when the text does not start with one, which
    callers read as "unusable".
    """
    match = _SEMVER_CORE.match(version.strip())
    return tuple(int(part) for part in match.group().split(".")) if match else ()
