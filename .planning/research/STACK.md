# Technology Stack: SOLID Refactoring Tooling

**Project:** Executor (Windows UI automation/replay), SOLID refactor
**Researched:** 2026-10-14
**Versions:** checked against the PyPI JSON API on 2026-10-14 (HIGH for version numbers). Tool-choice rationale is largely my own judgment, MEDIUM. I did not use Context7 or the official docs.

## Guiding Principle

Use the standard library for abstractions (`typing.Protocol`, `abc.ABC`, `dataclasses`). Use plain constructor injection with a single composition root (`main.py` or a `composition.py`). Add a tool only if it enforces something or protects behavior. No DI framework.

## Recommended Stack

### Abstractions (stdlib, no install)
| Technology | Purpose | Why | Confidence |
|---|---|---|---|
| `typing.Protocol` (+ `@runtime_checkable` sparingly) | ISP and DIP interfaces: `ElementFinder`, `ActionPerformer`, `InputBackend`, `TimelineLoader` | Structural typing lets existing classes satisfy interfaces without changing their inheritance, which suits incremental refactoring and the backward-compatibility constraint. Small role-specific protocols are the natural fit for ISP. | HIGH |
| `abc.ABC` | Only where shared behavior or a template method is needed (e.g. a base executor with a common replay loop) | Use it when the base carries code. Use Protocol when it only carries a contract. | HIGH |
| `dataclasses` (`frozen=True, slots=True`) | Value objects for actions, timeline events, resolved elements | Immutable value objects make LSP and OCP easier to reason about. | HIGH |
| `typing.override` (3.12) | Mark overrides explicitly | The type checker catches silent LSP drift when a base signature changes. | HIGH |
| `functools.singledispatch` or a dict registry | OCP for action types (replace the big Level0/Level1 if/elif ladders) | A registry of `action_type -> handler` lets you add actions without editing the dispatcher. | MEDIUM |

### Dependency Injection
| Choice | Why |
|---|---|
| **Manual constructor injection plus a composition root** | The project has roughly 4 executors and 5 pipeline stages. A container adds indirection for no gain. Pass `pyautogui` and `uiautomation` wrappers in as Protocol-typed adapters. This is also what makes fakes possible in tests. |
| Optional later: `wireup` 2.12.1 or `punq` 0.9.0 | Only if the composition root grows past about 100 lines. `punq` is tiny and type-hint driven. `wireup` is actively maintained (2.12.1, 2026-10-07) and has validation at startup. Do not adopt these in this milestone. |

### Static Typing and Linting
| Technology | Version | Purpose | Why |
|---|---|---|---|
| mypy | 2.4.0 | Verifies Protocol conformance and LSP signatures | Use `--strict` on new or refactored packages and ratchet per-module overrides in `pyproject.toml`. The legacy code will not pass strict mode on day one. Pick one checker: mypy has the more mature Protocol and plugin behavior. (pyright 1.1.414 is an acceptable alternative if the team lives in VS Code or Pylance. Do not run both.) |
| ruff | 0.17.0 | Lint and format in one tool | Rules to enable: `ARG` (unused args, a sign of ISP violations), `C90` (set `max-complexity` to about 10, a proxy for SRP), `PLR0911/0912/0913/0915` (too many returns, branches, args, statements), `B`, `SIM`, `UP`, `I`, `TID` (banned imports), `PL`. Replaces flake8, isort, and black. |
| import-linter | 2.15 | Architecture enforcement in CI | Declare layers (e.g. `cli > pipeline > executors > ports > adapters`). Use `forbidden` contracts so domain and executor code cannot import `pyautogui`, `uiautomation`, or `pywinauto` directly. This is the most direct automated check for DIP. |
| vulture | 2.16 | Find dead code after splitting classes | Run it on demand, not in CI, because it gives false positives with dynamic dispatch. |

### Testing for Refactoring Safety
| Technology | Version | Purpose | Why |
|---|---|---|---|
| pytest | 9.1.1 | Test runner | Existing tests are in `tests/`. Keep them and standardize on pytest. |
| pytest-cov | 7.1.0 | Coverage | Use branch coverage (`--cov-branch`) and gate on refactored modules, not on the whole repo. |
| pytest-mock | 3.16.0 | `mocker` fixture | Mock `pyautogui` and `uiautomation` at the boundary. Once the adapters are injected, prefer hand-written fakes that implement the Protocols. |
| syrupy | 6.1.1 | Snapshot / characterization tests | Capture current output (normalized actions, resolved elements, pipeline JSON) from the existing recordings such as the `AnotatedData` JSON, and assert it is unchanged after each refactor step. This is the safest net for legacy code you do not fully understand. Commit the snapshots. |
| hypothesis | 6.169.1 | Property tests | Use it for the pure logic: coalescing, scroll resolution, coordinate math, JSON normalization. Optional. |
| pytest-randomly | 5.0.0 | Random test order | Surfaces hidden shared state (module globals) left by the old design. |
| pytest-socket | 0.8.1 | Block network | Optional. Keeps tests from pulling HF models. |

Characterization-test rule: before touching a module, write snapshot tests against its current behavior using fakes for pyautogui and uiautomation. Then refactor, and the tests must still pass without edits.

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|---|---|---|---|
| DI | Manual injection | dependency-injector 4.49.1 | Heavy, wiring and decorator magic, Java-style. It conflicts with the "Pythonic, not Java-isms" constraint. |
| DI | Manual injection | lagom 2.7.7 | Last release was 2025-07. The project is not needed at this scale. |
| Architecture checks | import-linter | pytest-archon 0.0.7 | Less mature (0.0.x). import-linter has declarative contracts and layer support. |
| Type checker | mypy | pyright | Either works. Pick one. mypy was chosen for stricter Protocol and override checks and for CI use. |
| Lint/format | ruff | flake8 + black + isort + pylint | Redundant. ruff covers them. |
| Complexity metrics | ruff C90 | radon 6.0.1 | Last release was in 2023. ruff gives the same signal. |
| Snapshot | syrupy | approvaltests 19.1.1 | Both work. syrupy integrates natively with pytest. |
| Dead code | vulture | deadcode 2.4.1 | Not updated since 2024. |
| Interfaces | Protocol | `zope.interface` | Not idiomatic for this project. |

## Project-Specific Notes

- Third-party and embedded code: exclude OmniParser from ruff, mypy, and import-linter (`exclude`, `extend-exclude`, `ignore_missing_imports` or per-module overrides). Out of scope.
- Dependencies like `uiautomation`, `pywinauto`, and `pyautogui` ship no type stubs. Define your own narrow Protocols as ports and keep the untyped calls inside the adapter modules. That also confines the mypy ignores to one place.
- Tests must run without a live desktop. Gate any real-desktop tests behind a marker (`@pytest.mark.desktop`, deselected by default).
- The pinned `numpy==1.26.4`, `transformers==4.46.3`, and so on constrain resolution, so add the dev tools under `[dependency-groups] dev` and not under runtime dependencies.
- The Windows-only constraint means CI needs a Windows runner or the tests need fakes. Use fakes.

## Installation

```bash
uv add --dev pytest pytest-cov pytest-mock pytest-randomly syrupy hypothesis \
  ruff mypy import-linter vulture
```

```toml
[tool.ruff.lint]
select = ["E","F","I","B","UP","SIM","ARG","C90","PLR","TID"]
[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.mypy]
python_version = "3.12"
# per-package strict overrides, ratcheted as modules are refactored

[tool.importlinter]
root_packages = ["executor"]   # adjust to the new package layout

[tool.pytest.ini_options]
markers = ["desktop: requires a live Windows desktop"]
addopts = "--cov-branch"
```

## Sources

- PyPI JSON API (`https://pypi.org/pypi/<pkg>/json`), checked 2026-10-14. HIGH for versions and release dates.
- `pyproject.toml` in the repo, for current constraints.
- Rationale for the tool choices comes from my own judgment and general ecosystem knowledge. I did not verify it against Context7 or the official docs. MEDIUM. Worth confirming the mypy 2.x strict flags and the ruff 0.17 rule names when configuring.
