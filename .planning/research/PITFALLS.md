# Domain Pitfalls

**Domain:** SOLID refactoring of a Python Windows UI automation/replay system (~6.5k LOC, flat modules, few tests)
**Researched:** 2026-10-14
**Confidence:** MEDIUM (based on established refactoring practice and a codebase size check; no external sources were verified in this pass)

## Critical Pitfalls

### 1. Refactoring without a characterization safety net
**What goes wrong:** Only drag, scroll and typing finders have tests. Executors, mousemapper, click_content, main.py pipeline and the action classes have none. Refactoring them blind causes silent behavior changes.
**Why:** The real side effects (pyautogui, uiautomation, pynput, OmniParser/torch) cannot run in CI, so nobody tests them.
**Warning signs:** "Looks equivalent" reviews; tests that only assert "no exception"; the need to run on a live desktop to know if it works.
**Prevention:** Before touching a module, write characterization (golden) tests. Feed existing recorded JSON (e.g. the AnotatedData and DataMapper accessibility JSON samples) through the parsing/normalization/mapping code and snapshot the output. For executors, use a recording fake of the input backend and assert the exact ordered call list (click coords, key sequences, sleeps) for a fixed timeline. Keep a manual live-desktop smoke script for each executor type.
**Phase:** Phase 1 (test baseline), a hard gate before any extraction.

### 2. Behavior change hidden in "pure" extraction
**What goes wrong:** Moving code into new classes changes order of operations, timing (sleeps, focus waits), default arguments, mutable state, or exception handling (a swallowed exception now propagates, or the reverse).
**Warning signs:** Diffs that move and edit the same lines; replay that is "slightly flakier"; changed `time.sleep` placement.
**Prevention:** Separate commits: move verbatim first (tests green), then restructure. Preserve the timing constants and fallback order (AX first, then coordinates) explicitly. Never "clean up" while extracting.
**Phase:** every phase; enforce as a review rule.

### 3. Breaking public API and script entry points
**What goes wrong:** Files are moved into a package and `import executor`, `python main.py`, and the CLI/REPL scripts break. Other scripts import from each other by flat module name. Note the git status shows many deletions and untracked files, so the working tree is in flux.
**Prevention:** Keep thin re-export shims at the old module paths (with `__all__`) until a deprecation phase. Use `grep` to enumerate importers before moving. Commit or stabilize the working tree before starting. Do the package restructure after the logic extraction, not before.
**Detection:** ImportError on any entry script; add an import smoke test of every legacy module name.
**Phase:** package-structure phase (late); shims at the same time.

### 4. Over-engineering: Java-style abstraction explosion
**What goes wrong:** An interface per class, factories of factories, a DI container, one-method classes, and `IFoo`/`FooImpl` pairs, in 6k lines of scripts.
**Warning signs:** An ABC with exactly one implementation and no test fake; more files than behavior; a constructor taking more than about 5 collaborators; "future extensibility" justifications.
**Prevention:** Rule of two: introduce an abstraction only when there are at least two real implementations, or one real plus one test fake. The seams that clearly earn it here are: input backend (pyautogui), element lookup (uiautomation), the OmniParser detector, and the executor and action interfaces (4 existing variants). Everything else stays a plain function or dataclass. Prefer functions over single-method classes.
**Phase:** planning for every phase; make "justify the abstraction" a plan checklist item.

### 5. Wrong target for DIP: abstracting libraries instead of seams
**What goes wrong:** Wrapping all of pyautogui/uiautomation 1:1 yields a leaky mirror API that is no easier to fake. Or the abstraction exposes library types (`uiautomation.Control`) so callers still depend on the concrete.
**Prevention:** Define the Protocol in terms of domain needs (`click(point)`, `find_element(selector) -> ElementRef`), returning project dataclasses (the existing DataClasses) and not library objects. Keep the Protocol narrow.
**Phase:** abstraction/DIP phase.

## Moderate Pitfalls

### 6. Protocol vs ABC misuse
**What goes wrong:** ABCs used for pure structural contracts force inheritance; Protocols used where shared behavior or enforced instantiation errors are wanted. Forgetting `@runtime_checkable` while using isinstance (which also only checks method presence, not signatures). Protocols with attributes plus methods that are hard to satisfy.
**Prevention:** Protocol for consumer-side ports (backends, finders, parsers; tests pass fakes without inheritance). ABC (or a plain base class) only where shared template logic exists, such as a common executor run loop. Run mypy or pyright in CI so Protocols are actually checked; without a type checker they provide no guarantee. Avoid isinstance on Protocols; dispatch via a registry/dict.
**Phase:** interface-definition phase; add the type checker early.

### 7. DI anti-patterns
**What goes wrong:** Service locator or global registry; default arguments that instantiate real backends at import time (`def __init__(self, backend=PyAutoGuiBackend())`, with the mutable-default and import-side-effect problems, and torch loading on import); DI frameworks; passing a god "context" object.
**Prevention:** Constructor injection with `None` defaults resolved inside `__init__` or in a single composition root (main.py / a `build_*` function). Keep heavy imports (torch, transformers, OmniParser) lazy so tests import fast. No framework.
**Detection:** tests that need a display or GPU just to import.
**Phase:** DIP phase and the pipeline-wiring phase.

### 8. SOLID principles conflicting
- **SRP vs simplicity:** Splitting parse/normalize/execute into many tiny classes scatters one workflow. Split by reason to change (timeline format, element resolution, input backend), not by verb.
- **OCP vs YAGNI:** Plugin/strategy registries for action types before a second variant exists. Level0/Level1 action classes may be fine as a dispatch table; decompose only where the big classes actually change independently.
- **ISP vs DRY/cohesion:** Many tiny Protocols multiply the type surface. Segregate by client (executor needs `click/type/scroll/drag`; the finder needs only `find`).
- **LSP vs the four executor variants:** The variants likely differ in input shape (timeline vs live AX vs drag-drop) and cannot share one `execute()` signature without becoming a lowest-common-denominator or raising NotImplementedError. A forced shared base violates LSP. Prefer separate narrow interfaces (a step-executor Protocol for resolve/perform) and compose; do not unify the REPL or CLI with the replay engines.
- **DIP vs performance/timing:** Extra indirection in tight replay loops is negligible, but wrapper layers adding implicit waits are not; keep timing in one place.
**Prevention:** Record each tradeoff decision in PROJECT.md Key Decisions; default to the simpler option.
**Phase:** design at the start of each phase.

### 9. Fakes that diverge from reality
**What goes wrong:** Tests pass against a fake backend, yet real Windows behavior (DPI scaling, multi-monitor coordinates, focus, UIA timing) differs. Refactor regressions then appear only live.
**Prevention:** Keep a small contract test suite run against both fake and real backends (marked `@pytest.mark.windows_live`, opt-in). Keep a manual replay checklist per phase. Test DPI-sensitive coordinate math as pure functions.
**Phase:** test-infrastructure phase.

### 10. Dataclass and data-format drift
**What goes wrong:** Introducing typed dataclasses or Pydantic models over the JSON timelines changes serialization (missing keys, extra keys, key order, numeric types), and breaks existing recorded data files or the pipeline stage handoffs.
**Prevention:** Round-trip test every existing sample JSON (load, model, dump, equal). Be lenient on unknown keys. Do not rename fields or JSON keys in a refactor.
**Phase:** data-model phase.

### 11. Module-level side effects and globals
**What goes wrong:** Scripts (ax_tree_graph, automate_ax_click_matcher, main) run work at import or rely on module globals, `__main__` blocks and `sys.path` hacks; extracting classes drags those along, and tests import them and trigger pynput listeners or screen capture.
**Prevention:** Move behavior into functions with `if __name__ == "__main__"` guards first (cheap and safe); then extract.
**Phase:** early cleanup phase.

### 12. Splitting the REPL/CLI badly
**What goes wrong:** `accessibility_tree_executor.py` (754 lines) mixes finder, performer and REPL. Extraction can break interactive state (shared caches, current-tree state).
**Prevention:** Identify shared mutable state first; make it an explicit object passed in. Keep the REPL as a thin adapter over the extracted services.
**Phase:** executor SRP phase.

## Minor Pitfalls

### 13. Duplicate near-copies refactored inconsistently
`executor.py` vs `accessibility_tree_executor.py` vs `timeline_ax_executor.py` vs `drag_drop_executor.py` likely contain duplicated logic. Dedupe only after a characterization test proves the copies are truly identical; subtle differences are often intentional.

### 14. Coverage as the goal
Chasing a coverage percentage with mock-heavy tests that assert implementation details breaks on every refactor. Assert observable outputs (action call lists, mapped results).

### 15. Big-bang branch and mixed-in feature work
Long-lived refactor branches diverge; the scope rule forbids new features. Merge per principle or module, each green.

### 16. Inheritance introduced for "OCP"
Deep base-class hierarchies for actions. Prefer composition and a dispatch dict of callables or small strategy dataclasses.

### 17. Touching embedded OmniParser
Out of scope. Wrap it behind a Protocol adapter only; do not edit its code or reformat it.

### 18. Tooling churn
Mass reformatting or renames mixed with logic changes obscure review. Run formatters in separate commits. Dependency changes (pyproject.toml and uv.lock are already modified) should be settled before starting.

## Phase-Specific Warnings

| Phase topic | Likely pitfall | Mitigation |
|-------------|---------------|------------|
| Baseline tests | #1, #9, #14 | Golden-file and recording-fake tests; opt-in live suite |
| Import-safety cleanup | #11 | Main guards, lazy heavy imports |
| Data models | #10 | Round-trip tests on existing JSON |
| SRP split of executors | #2, #12, #13 | Move verbatim then restructure; explicit shared state |
| Interfaces (ISP/LSP) | #6, #8 | Narrow Protocols, no forced unified executor; add mypy/pyright |
| DIP and wiring | #5, #7 | Domain-shaped ports, composition root, no containers |
| OCP for actions | #4, #16 | Dispatch table; abstraction only with a second implementation |
| Package restructure | #3 | Re-export shims, import smoke test, do last |

## Pythonic overrides (when to stop applying SOLID)
- Pure functions and dataclasses beat classes when there is no state.
- A module is a fine singleton or namespace; no need for a class.
- Duck typing with a Protocol, not inheritance.
- If an abstraction has one implementation and no test needs a fake, leave it concrete.
- Scripts such as ax_tree_graph*.py (visualization/debug utilities) may only need import-safety, not full SOLID treatment.

## Sources
- Project context: .planning/PROJECT.md and a module size survey (accessibility_tree_executor.py 754 LOC, node.py 888, mousemapper.py 682, executor.py 657, click_content.py 605). Confidence MEDIUM.
- Established practice: Feathers, characterization tests; Fowler, Refactoring; PEP 544 (Protocols). Not re-verified online in this pass (LOW to MEDIUM).
