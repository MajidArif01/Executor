# Requirements: Executor — SOLID Refactoring

**Defined:** 2026-10-14
**Core Value:** Clean, maintainable architecture following SOLID principles while preserving all existing automation and replay functionality without regressions.

## v1 Requirements

Requirements for the SOLID refactoring milestone. Each maps to roadmap phases.

### Safety Net

- [ ] **SAFE-01**: Characterization (snapshot) tests capture current behavior of timeline normalizer, NaiveExecutor dry-run, and action validators
- [ ] **SAFE-02**: Golden-file tests validate mousemapper, node.py, and click_content enrichment on sample JSON
- [ ] **SAFE-03**: Recording fakes for executors assert exact ordered action call lists
- [ ] **SAFE-04**: Import smoke test verifies every legacy module imports without error
- [ ] **SAFE-05**: pytest configured with `desktop` marker deselected by default

### Hygiene

- [ ] **HYGN-01**: Working tree stabilized — canonical copy chosen for each duplicate file
- [ ] **HYGN-02**: `DataMapper.py/` directory renamed to valid Python package name
- [ ] **HYGN-03**: `__main__` guards added to scripts with import-time side effects

### Domain Types (SRP)

- [ ] **DOMN-01**: Frozen dataclasses for `Action`, `ElementQuery`, `ElementInfo`, `ElementRef`, `Rect` extracted to domain package
- [ ] **DOMN-02**: Timeline loader and normalizer extracted from `executor.py` into `timeline/` package
- [ ] **DOMN-03**: Import coupling between `timeline_ax_executor`, `executor`, and `accessibility_tree_executor` broken

### Ports and Adapters (DIP/ISP)

- [ ] **PORT-01**: `InputBackend` Protocol defined with `PyAutoGuiBackend` adapter
- [ ] **PORT-02**: `NullBackend` replaces scattered `if not dry_run` checks
- [ ] **PORT-03**: `FakeBackend` enables tests without a live desktop
- [ ] **PORT-04**: `ElementLocator` Protocol defined with `UiAutomationLocator` adapter
- [ ] **PORT-05**: All pyautogui/uiautomation/pywinauto/pynput imports confined to adapter modules

### Action System (OCP)

- [ ] **ACTN-01**: Action registry replaces if/elif dispatch in Level0/Level1
- [ ] **ACTN-02**: One handler per action type, each depending on `InputBackend` Protocol
- [ ] **ACTN-03**: `ActionLevel0` and `ActionLevel1` preserved as delegating shims for backward compatibility

### Executor Architecture (LSP/SRP)

- [ ] **EXEC-01**: `ReplayEngine` extracted with loop, error policy, dry-run, and delay logic
- [ ] **EXEC-02**: Resolution strategies implemented: `CoordinateStrategy`, `AxFirstStrategy`, `DragLiveStrategy`
- [ ] **EXEC-03**: `NaiveExecutor`, `TimelineAxExecutor`, `DragDropExecutor` preserved as thin facades
- [ ] **EXEC-04**: Contract tests validate all executor facades against shared `Executor` Protocol

### AX Layer (SRP)

- [ ] **AXLR-01**: `accessibility_tree_executor.py` split into locator, performer, and REPL components
- [ ] **AXLR-02**: Colliding `Executor` class renamed with backward-compatible alias
- [ ] **AXLR-03**: Fake locators with scripted trees enable unit testing

### Pipeline (DIP/OCP)

- [ ] **PIPE-01**: `Stage` Protocol and `StageContext` dataclass defined
- [ ] **PIPE-02**: `ArgvModuleStage` adapter wraps legacy `main(argv)` functions
- [ ] **PIPE-03**: `PipelineRunner` accepts injected ordered list of stages

### Package Structure

- [ ] **PKGS-01**: Final package layout with `core`, `backends`, `executors`, `pipeline`, `cli` packages
- [ ] **PKGS-02**: Re-export shims at old module paths preserve backward compatibility
- [ ] **PKGS-03**: mypy strict on new packages, ruff configured with architecture-enforcing rules

### Static Analysis

- [ ] **STAT-01**: ruff configured with `ARG`, `C90`, `PLR`, `B`, `SIM`, `UP`, `I`, `TID` rules
- [ ] **STAT-02**: mypy configured strict on refactored packages
- [ ] **STAT-03**: import-linter contracts forbid domain/executor code from importing backend libraries

## v2 Requirements

Deferred to future milestone. Tracked but not in current roadmap.

### Extended Testing

- **TEST-01**: Hypothesis property tests for pure coordinate and scroll math
- **TEST-02**: Coverage gate enforced in CI for refactored modules

### Advanced Patterns

- **ADVN-01**: Plugin entry points for external action type extensibility
- **ADVN-02**: `FallbackExecutor(primary, secondary)` built by composition
- **ADVN-03**: `@runtime_checkable` Protocol conformance tests
- **ADVN-04**: Injectable sleep and clock for deterministic timing tests

## Out of Scope

| Feature | Reason |
|---------|--------|
| New automation features | This is a refactoring effort, not feature work |
| OmniParser modifications | Embedded third-party code, excluded from refactoring |
| Cross-platform support | Remains Windows-only, not a goal of SOLID refactoring |
| DI framework (dependency-injector, wireup, etc.) | Project too small; manual injection sufficient |
| Deep executor inheritance tree | Anti-pattern; use composition and strategies instead |
| One interface per class (IFoo pattern) | Java-ism; Python uses Protocol at real seams only |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| SAFE-01 | Phase 1 | Pending |
| SAFE-02 | Phase 1 | Pending |
| SAFE-03 | Phase 1 | Pending |
| SAFE-04 | Phase 1 | Pending |
| SAFE-05 | Phase 1 | Pending |
| HYGN-01 | Phase 1 | Pending |
| HYGN-02 | Phase 1 | Pending |
| HYGN-03 | Phase 1 | Pending |
| DOMN-01 | Phase 2 | Pending |
| DOMN-02 | Phase 2 | Pending |
| DOMN-03 | Phase 2 | Pending |
| PORT-01 | Phase 2 | Pending |
| PORT-02 | Phase 2 | Pending |
| PORT-03 | Phase 2 | Pending |
| PORT-04 | Phase 2 | Pending |
| PORT-05 | Phase 2 | Pending |
| ACTN-01 | Phase 3 | Pending |
| ACTN-02 | Phase 3 | Pending |
| ACTN-03 | Phase 3 | Pending |
| EXEC-01 | Phase 3 | Pending |
| EXEC-02 | Phase 3 | Pending |
| EXEC-03 | Phase 3 | Pending |
| EXEC-04 | Phase 3 | Pending |
| AXLR-01 | Phase 3 | Pending |
| AXLR-02 | Phase 3 | Pending |
| AXLR-03 | Phase 3 | Pending |
| PIPE-01 | Phase 4 | Pending |
| PIPE-02 | Phase 4 | Pending |
| PIPE-03 | Phase 4 | Pending |
| PKGS-01 | Phase 4 | Pending |
| PKGS-02 | Phase 4 | Pending |
| PKGS-03 | Phase 4 | Pending |
| STAT-01 | Phase 4 | Pending |
| STAT-02 | Phase 4 | Pending |
| STAT-03 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 35 total
- Mapped to phases: 35
- Unmapped: 0 ✓

---
*Requirements defined: 2026-10-14*
*Last updated: 2026-10-14 after initial definition*
