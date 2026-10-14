# Research Summary: SOLID Refactor of Executor

**Overall confidence:** MEDIUM. Tool versions were verified against PyPI on 2026-10-14. Current-state facts come from code reading. Design patterns are standard Python practice from training knowledge, not re-verified.

## 1. Key Recommendations
1. **Characterization tests come first and are a hard gate.** Only the drag, scroll and typing finders have tests. Snapshot the existing recorded JSON through the normalizer and mappers. Use recording fakes for executors.
2. **Stabilize the working tree before anything else.** Git shows many deletions, untracked files, and modified `pyproject.toml` and `uv.lock`. Pick the canonical copy of each duplicate (`mousemapper.py` vs `DataMapper.py/mousemapper.py`, `find_element.py` vs `find_ui_element.py`). Rename the `DataMapper.py/` directory.
3. **Use one `ReplayEngine` plus a `ResolutionStrategy`, not an executor class hierarchy.** The four executors differ in target resolution, not in the loop. Keep `NaiveExecutor`, `TimelineAxExecutor` and `DragDropExecutor` as thin backward-compatible facades.
4. **Conflict resolved between FEATURES and PITFALLS.** FEATURES wants one `Executor` Protocol for all variants. PITFALLS warns that forcing one signature violates LSP. Decision: a narrow `run(actions) -> list[ActionResult]` Protocol for the replay facades only. Exclude the REPL/CLI and the interactive live-AX executor.
5. **Abstract only at real seams.** These are the input backend (pyautogui), the element locator (uiautomation), the action registry, the pipeline stage, and the OmniParser adapter. A new abstraction needs two real implementations, or one real plus one test fake.
6. **Move first, restructure second.** Never move and edit the same lines in one commit. Preserve sleeps, defaults and the AX-then-coordinates fallback order.
7. **Package layout and shim removal come last.** `main.py` resolves stages by module name, so old module names stay as re-export shims.
8. **No DI framework, no new features, no OmniParser edits.**

## 2. Stack Summary
- **Abstractions (stdlib):** `typing.Protocol` for ports. `abc.ABC` only for a real template method. Frozen `dataclasses` (`slots=True`). `typing.override` (Python 3.12). A dict or `singledispatch` registry for actions.
- **DI:** manual constructor injection with `None` defaults resolved in `__init__`, plus one composition root (`build_default_engine()` or `main.py`). No wireup, punq, dependency-injector or lagom.

| Tool | Version | Role |
|---|---|---|
| pytest | 9.1.1 | Runner, with a `desktop` marker deselected by default |
| pytest-cov | 7.1.0 | Branch coverage, gated on refactored modules only |
| syrupy | 6.1.1 | Snapshot and characterization tests |
| pytest-mock | 3.16.0 | Boundary mocking until fakes exist |
| pytest-randomly | 5.0.0 | Exposes hidden global state |
| hypothesis | 6.169.1 | Optional, for pure coordinate and scroll math |
| ruff | 0.17.0 | Lint and format |
| mypy | 2.4.0 | Strict only on new packages, ratcheted per module |
| import-linter | 2.15 | Forbids domain code from importing pyautogui, uiautomation, pywinauto |
| vulture | 2.16 | On-demand dead-code check |

## 3. Architecture Summary
Layered ports-and-adapters with dependencies pointing inward: cli → application (`PipelineRunner`, `ReplayEngine`) → domain (frozen dataclasses, `ActionRegistry`) → ports (Protocols) → infrastructure adapters.

- **domain:** `Action`, `ElementQuery`, `ElementInfo`, `ElementRef`, `Rect`. Pure, no I/O.
- **timeline:** loader and normalizer extracted from `executor.py`.
- **actions:** `InputBackend` Protocol, `PyAutoGuiBackend`, `NullBackend` (dry run), `FakeBackend` (tests), the registry, one handler per action type.
- **replay:** `ReplayEngine` (loop, error policy, dry-run, delay). Strategies: `CoordinateStrategy`, `AxFirstStrategy`, `DragLiveStrategy`.
- **ax:** `ElementLocator` Protocol, `UiAutomationLocator`, matching, performer, REPL.
- **pipeline:** `Stage` Protocol, `StageContext`, `PipelineRunner`, `ArgvModuleStage` adapter.

## 4. Refactoring Order

| Phase | Delivers | Risk |
|---|---|---|
| 0. Hygiene and safety net | Clean tree, canonical duplicates, pytest setup, snapshot tests, import smoke test | Low |
| 1. Domain types and normalizer | `timeline/` and `domain/` packages, breaks import coupling | Low |
| 2. Ports and adapters | `InputBackend` and `ElementLocator` Protocols, adapters, fakes, mypy | Medium |
| 3. Action registry and handlers (OCP) | Handlers, registry, Level0/Level1 shims | Medium |
| 4. ReplayEngine and Executor Protocol | Loop extraction, facades, contract tests | Medium |
| 5. AX layer split (riskiest) | Locator/performer/REPL split, strategies, live smoke test | High |
| 6. Pipeline stages (parallel with 2-5) | Stage Protocol, StageContext, PipelineRunner | Medium |
| 7. Layout, tooling and shim removal | Final packages, import-linter, entry points | Low |

## 5. Critical Pitfalls
1. **No safety net.** Silent behavior change in untested code. Prevention: golden snapshots first.
2. **Hidden behavior change in extraction.** Sleeps, defaults, fallback order shift. Prevention: separate move and restructure commits.
3. **Broken imports and entry points.** Prevention: re-export shims, import smoke test, restructure last.
4. **Java-style over-engineering.** Prevention: rule of two, functions over single-method classes.
5. **Leaky abstractions.** Prevention: narrow domain-shaped ports, opt-in desktop contract suite.

## 6. Open Questions
- Which duplicate files are canonical and which git deletions are intended?
- Is `DataMapper.py/` directory naming intentional?
- Which executor variants can honestly share the `run(actions)` Protocol?
- Mypy or pyright? Recommendation is mypy.
- Who runs the manual live-desktop smoke checklist?
- Does external code import the legacy module names?
