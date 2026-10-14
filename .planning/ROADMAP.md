# Roadmap: Executor — SOLID Refactoring

## Overview

Incremental SOLID refactor of a Windows UI automation and replay system. Safety net first, then pure domain types and ports, then the action registry and ReplayEngine, then the riskiest step (AX layer split), and finally pipeline stages, package layout, shims and static-analysis enforcement. Every phase must leave the working system intact; move first, restructure second.

## Phases

- [ ] **Phase 1: Safety Net and Hygiene** - Stable tree, characterization tests, pytest setup
- [ ] **Phase 2: Domain Types and Ports** - Frozen domain types, timeline package, InputBackend and ElementLocator ports with adapters and fakes
- [ ] **Phase 3: Action Registry and ReplayEngine** - OCP action handlers and one ReplayEngine with strategies and thin executor facades
- [ ] **Phase 4: AX Layer Split** - Split accessibility_tree_executor into locator, performer and REPL (highest risk)
- [ ] **Phase 5: Pipeline, Package Layout and Enforcement** - Stage Protocol, PipelineRunner, final packages, shims, ruff/mypy/import-linter

## Phase Details

### Phase 1: Safety Net and Hygiene

**Goal**: Refactoring can begin safely because current behavior is pinned by tests and the working tree is stable
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: SAFE-01, SAFE-02, SAFE-03, SAFE-04, SAFE-05, HYGN-01, HYGN-02, HYGN-03
**Success Criteria** (what must be TRUE):

  1. `pytest` runs green from a clean checkout without a live desktop (`desktop` marker deselected by default)
  2. Snapshot tests of the timeline normalizer, NaiveExecutor dry-run and action validators, plus golden-file tests of mousemapper, node.py and click_content on sample JSON, fail if behavior changes
  3. Recording fakes let a test assert the exact ordered action calls an executor makes
  4. Every legacy module imports without side effects or errors (import smoke test passes)
  5. Each duplicate file has one canonical copy and `DataMapper.py/` is a valid package name

**Plans**: 8 plans
Plans:
**Wave 1**

- [ ] 01-01-PLAN.md - Commit working tree baseline, update .gitignore

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 01-02-PLAN.md - Resolve duplicates, pending deletions, DataMapper.py/ (checkpoint)

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 01-03-PLAN.md - pytest infra, dev deps, desktop marker, fakes and fixtures

**Wave 4** *(blocked on Wave 3 completion)*

- [ ] 01-04-PLAN.md - Normalizer snapshots, validator tests, JSON round trip
- [ ] 01-05-PLAN.md - NaiveExecutor recording-fake and dry-run tests
- [ ] 01-06-PLAN.md - mousemapper and node.py golden tests
- [ ] 01-07-PLAN.md - click_content golden tests
- [ ] 01-08-PLAN.md - Import smoke test and __main__ guard verification

### Phase 2: Domain Types and Ports

**Goal**: Pure domain types and narrow ports exist so core logic can run with no desktop libraries
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: DOMN-01, DOMN-02, DOMN-03, PORT-01, PORT-02, PORT-03, PORT-04, PORT-05
**Success Criteria** (what must be TRUE):

  1. Timeline loading and normalization is importable from `timeline/` and produces output identical to the Phase 1 snapshots
  2. `Action`, `ElementQuery`, `ElementInfo`, `ElementRef`, `Rect` are frozen dataclasses importable without pyautogui/uiautomation
  3. A dry run goes through `NullBackend` with no scattered `if not dry_run` checks, and tests run against `FakeBackend` with no live desktop
  4. pyautogui, uiautomation, pywinauto and pynput imports appear only in adapter modules
  5. Importing one executor module no longer drags in the others

**Plans**: TBD

### Phase 3: Action Registry and ReplayEngine

**Goal**: New action types and resolution behaviors can be added without editing existing dispatch code, and executors share one replay loop
**Mode:** mvp
**Depends on**: Phase 2
**Requirements**: ACTN-01, ACTN-02, ACTN-03, EXEC-01, EXEC-02, EXEC-03, EXEC-04
**Success Criteria** (what must be TRUE):

  1. Registering a new action handler works without modifying existing handlers or dispatch code
  2. `ActionLevel0` and `ActionLevel1` still work for existing callers via delegating shims
  3. `NaiveExecutor`, `TimelineAxExecutor` and `DragDropExecutor` replay the same timelines with the same ordered actions, sleeps and AX-then-coordinate fallback as before
  4. Contract tests run every executor facade against the shared `Executor` Protocol using fakes

**Plans**: TBD

### Phase 4: AX Layer Split

**Goal**: The accessibility executor is decomposed into locator, performer and REPL without changing live behavior
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: AXLR-01, AXLR-02, AXLR-03
**Success Criteria** (what must be TRUE):

  1. Element finding, action performing and the interactive REPL live in separate components, and the REPL still works from the command line
  2. The old `Executor` name still imports via a backward-compatible alias, and the renamed class no longer collides with the replay `Executor` Protocol
  3. Locator and performer logic is unit-tested using fake locators with scripted trees
  4. A manual live-desktop smoke checklist passes for AX click, scroll, typing and drag

**Plans**: TBD

### Phase 5: Pipeline, Package Layout and Enforcement

**Goal**: The pipeline is composed by injection, code lives in its final packages with old paths still working, and the architecture is enforced by tooling
**Mode:** mvp
**Depends on**: Phase 4
**Requirements**: PIPE-01, PIPE-02, PIPE-03, PKGS-01, PKGS-02, PKGS-03, STAT-01, STAT-02, STAT-03
**Success Criteria** (what must be TRUE):

  1. `PipelineRunner` runs an injected ordered list of stages, and legacy `main(argv)` stages work through `ArgvModuleStage`
  2. The full pipeline produces the same outputs as before on sample data
  3. Code is organized into `core`, `backends`, `executors`, `pipeline`, `cli`, and every old module path still imports via re-export shims
  4. ruff, mypy (strict on new packages) and import-linter pass, and import-linter fails if domain or executor code imports a backend library

**Plans**: TBD

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Safety Net and Hygiene | 0/TBD | Not started | - |
| 2. Domain Types and Ports | 0/TBD | Not started | - |
| 3. Action Registry and ReplayEngine | 0/TBD | Not started | - |
| 4. AX Layer Split | 0/TBD | Not started | - |
| 5. Pipeline, Package Layout and Enforcement | 0/TBD | Not started | - |
