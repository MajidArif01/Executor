# Feature Landscape: SOLID Refactoring of a Python Windows UI Automation System

**Domain:** Python refactoring (replay/automation executors, pipeline stages, action primitives)
**Researched:** 2026-10-14
**Confidence:** MEDIUM. Based on established Python community practice (typing.Protocol per PEP 544, ABC, dataclasses, "Pythonic DI"), tailored to PROJECT.md. No external sources were fetched this session. Treat specifics as well-known practice, not freshly verified.

## Guiding Principle

Python favors structural typing, first-class functions, and modules as namespaces. Apply SOLID where it gives a real seam (testing the OS boundary, swapping resolvers, extending action types). Skip it where it only adds ceremony.

## Table Stakes (must have for SOLID compliance)

| Feature | Principle | Why Expected | Complexity | Notes |
|---|---|---|---|---|
| Split executor classes by responsibility: timeline loading/parsing, action normalization, element resolution, action performing, CLI/REPL | SRP | Core stated problem (`executor.py`, `accessibility_tree_executor.py`) | High | Do first. Pure functions or small classes. Move REPL/`__main__` into separate `cli` module. |
| Common `Executor` Protocol (single method such as `execute(action) -> Result`, or `run(timeline)`) | DIP, LSP, ISP | Four executor variants share no contract | Low | Prefer `typing.Protocol`. Use ABC only when sharing implementation (template method). |
| Narrow Protocols for OS boundary: `InputDriver` (click, scroll, drag, type, hotkey) wrapping pyautogui/pynput | DIP, ISP | `TimelineAxExecutor` calls pyautogui/uiautomation directly; blocks testing | Medium | One thin adapter class per library. Keep Protocols 1-4 methods. |
| `ElementLocator`/`AccessibilityBackend` Protocol wrapping uiautomation/pywinauto lookups | DIP | Needed to unit test AX fallback without a live desktop | Medium | Fakes in tests, real adapter in prod. |
| Constructor injection of collaborators with sensible defaults (`def __init__(self, driver: InputDriver | None = None)`) | DIP | Preserves backward-compatible public API while enabling tests | Low | Defaults keep existing call sites working. |
| Composition root: wire concrete implementations in one place (`main.py`/factory) | DIP | Pipeline stages currently wired by import | Medium | Plain function, no DI container. |
| Pipeline stage contract (Protocol or plain callable type: input dataclass -> output dataclass) | DIP, OCP | Stages called by convention only | Medium | Typed dataclasses at stage boundaries (dataclasses already exist). |
| Action dispatch via registry (`dict[str, handler]`) replacing if/elif chains and big Level0/Level1 classes | OCP | Adding an action type should not edit the executor | Medium | Register with a decorator or dict. Preserve Level0/Level1 names as facades. |
| Break Level0/Level1 into cohesive groups (mouse, keyboard, scroll, drag) behind a facade | SRP, ISP | Large catch-all classes | Medium | Keep old classes as thin delegating wrappers for compat. |
| LSP audit: all executor variants honor the same pre/postconditions, return types, exceptions | LSP | Variants must be interchangeable | Low-Med | Write one shared contract test suite run against every variant. |
| Characterization (golden) tests before moving code | All | Constraint: no regressions, test-driven | Medium | Capture current behavior with fakes. Existing drag/scroll/typing finder tests are the seed. |
| Fake/stub implementations of the Protocols for tests (no live desktop) | DIP | Makes refactor verifiable in CI | Medium | pytest fixtures. Real-desktop tests marked and skipped by default. |
| Package structure with clear boundaries (e.g. `core/` protocols and models, `backends/` windows adapters, `executors/`, `pipeline/`, `cli/`) | SRP | Stated Active requirement | Medium | Dependencies point inward: backends and executors depend on core, never the reverse. Avoid circular imports. |
| Preserve public import paths (re-export shims) | Compat | Stated constraint | Low | `executor.py` etc. re-export from new locations. Deprecate later. |
| Separate import-time side effects from definitions (`if __name__ == "__main__"`) | SRP | Scripts at root mix demo and library code | Low | Also avoids heavy torch import in lightweight paths. |

## Differentiators (beyond basic SOLID; worth it only if cheap)

| Feature | Value | Complexity | Notes |
|---|---|---|---|
| `@runtime_checkable` Protocols plus a contract test that asserts each backend conforms | Catches drift | Low | Runtime check verifies only method presence, not signatures. Use mypy/pyright for signatures. |
| Static type checking (pyright/mypy) in CI on the new core package | Enforces DIP/LSP mechanically | Medium | Start with `core/` and `executors/` only. Gradual strictness. |
| Result/outcome dataclass (`ActionResult` with status, fallback used) instead of bare bools/prints | Testability, observability of AX-to-coordinate fallback | Low-Med | Frozen dataclasses. |
| Fallback chain as composition (`FallbackExecutor(primary, secondary)`) replacing inheritance | OCP, LSP | Medium | Fits TimelineAxExecutor (AX then coordinates). Composition beats subclassing here. |
| Injectable clock/sleep and screen-size provider | Deterministic replay tests | Low | A `sleep: Callable[[float], None]` parameter is enough. |
| Dependency direction check (import-linter) | Prevents layering regressions | Low-Med | Optional, config-only. |
| Entry-point/registry plugin discovery for new action types | Extensibility | High | Only if third-party action types are actually wanted. Likely not now. |
| Coverage gate on refactored modules | Regression safety | Low | Target meaningful coverage on logic, not OS adapters. |

## Anti-Features (deliberately do NOT do)

| Anti-Feature | Why Avoid | Do Instead |
|---|---|---|
| Interface per class (`IFoo` naming, one interface with one implementation and no test seam) | Java-ism, noise | Add a Protocol only at real seams: OS boundary, resolver strategies, executors, pipeline stages. |
| DI container/framework (dependency-injector, etc.) | Heavy for a CLI/library | Constructor injection plus a composition-root function. |
| Abstract factory / factory-of-factories / builder for simple objects | Over-engineering | Plain functions or dataclass constructors. |
| Strategy classes for what could be a function | Python has first-class callables | Use `Callable[...]` or a dict of functions. |
| Deep inheritance hierarchies of executors | Violates LSP, hard to follow | Composition plus a shared Protocol. Shallow ABC only for a real template method. |
| Getters/setters, private-field ceremony | Java-ism | Plain attributes, `@property` only when needed. |
| Splitting into one-method classes everywhere (SRP taken literally) | Fragmentation, indirection | Split by reason to change, not by method. A module of functions is fine. |
| Wrapping everything in Protocols including pure data (use dataclasses directly) | Needless | Dataclasses for data, Protocols for behavior. |
| Wrapping or modifying embedded OmniParser | Out of scope | Wrap it behind one adapter at the project boundary only. |
| Big-bang rewrite or renaming public APIs | Violates constraint | Incremental moves with shims and tests green at each step. |
| Mock-heavy tests asserting call sequences | Brittle, locks in implementation | Hand-written fakes asserting observable outcomes. |
| ISP micro-fragmentation (a Protocol per method) | Over-engineering | Protocols per client need, 1-4 methods. |
| Abstracting the live-desktop UI tests away entirely | Can't prove real behavior | Keep a small marked smoke suite against real Windows. |

## Feature Dependencies

```
Characterization tests (+ fakes) ──> every other refactor step
Package structure + core models/Protocols
  ├─> OS-boundary Protocols (InputDriver, AccessibilityBackend) ──> constructor injection ──> composition root
  ├─> Executor Protocol ──> LSP contract test suite ──> FallbackExecutor (differentiator)
  ├─> Pipeline stage contracts ──> composition root (main.py)
  └─> Action registry ──> Level0/Level1 split (facades preserved)
SRP split of executors ──> needs OS-boundary Protocols (to inject) and Action registry
Public API re-export shims ──> run alongside every move
Static typing / import-linter ──> after Protocols and package layout settle
```

## MVP Recommendation (ordering for roadmap)

1. Characterization tests, fakes, pytest setup (enables all else).
2. Core models and Protocols (Executor, InputDriver, AccessibilityBackend, pipeline stage) plus package skeleton and compat shims.
3. OS-boundary adapters and constructor injection with defaults (DIP).
4. SRP split of `executor.py`, `accessibility_tree_executor.py`, `timeline_ax_executor.py`; CLI/REPL extraction.
5. Action registry and Level0/Level1 decomposition (OCP, ISP).
6. LSP shared contract tests across executors; pipeline composition root.

Defer: plugin entry points, import-linter, strict typing everywhere, FallbackExecutor rewrite (do only if the existing inheritance or duplication blocks LSP).

## Python vs Java-ism Quick Reference

| Translates well | Java-ism to avoid |
|---|---|
| Protocol for structural interfaces (ISP, DIP) | `I`-prefixed interface for every class |
| Constructor injection with defaults | DI containers, service locators |
| Dict/decorator registries (OCP) | Visitor/abstract factory for simple dispatch |
| Composition and small functions | Deep ABC trees, template methods everywhere |
| Frozen dataclasses for stage I/O | Beans with getters/setters |
| Fakes at the OS boundary | Mocking every collaborator |

## Sources

- PEP 544 (typing.Protocol), Python `abc` and `dataclasses` docs. Not re-fetched this session.
- Community practice on Pythonic DI and SOLID (Cosmic Python "Architecture Patterns with Python", Brandon Rhodes on Python design patterns). From training knowledge; MEDIUM confidence.
- `.planning/PROJECT.md` for project-specific constraints.
