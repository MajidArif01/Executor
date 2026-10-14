# Executor — SOLID Refactoring

## What This Is

A Windows desktop UI automation and replay system that records user interactions (clicks, scrolls, keyboard input, drag-and-drop), enriches them with accessibility tree and OmniParser element data, and replays them on the live desktop. This project restructures the existing codebase to follow SOLID principles of software engineering for improved maintainability, testability, and extensibility.

## Core Value

Clean, maintainable architecture following SOLID principles while preserving all existing automation and replay functionality without regressions.

## Requirements

### Validated

- ✓ Coordinate-based action replay (NaiveExecutor) — existing
- ✓ Accessibility-tree-based element lookup and execution — existing
- ✓ Timeline replay with AX fallback to coordinates (TimelineAxExecutor) — existing
- ✓ Drag-and-drop replay with live element resolution — existing
- ✓ OmniParser integration for screenshot element detection — existing
- ✓ Mouse event to element mapping (mousemapper) — existing
- ✓ Typing event to element mapping (node.py) — existing
- ✓ Accessibility tree enrichment pipeline — existing
- ✓ Click content cropping for VLM input — existing
- ✓ Pipeline orchestration (main.py) — existing
- ✓ Two-layer action primitives (Level0 atomic, Level1 macros) — existing
- ✓ Scroll target resolution and coalescing — existing
- ✓ Unit tests for drag, scroll, and typing element finders — existing

### Active

- [ ] Apply Single Responsibility Principle — each class/module has one reason to change
- [ ] Apply Open/Closed Principle — extend behavior without modifying existing code
- [ ] Apply Liskov Substitution Principle — subtypes are substitutable for their base types
- [ ] Apply Interface Segregation Principle — clients depend only on interfaces they use
- [ ] Apply Dependency Inversion Principle — depend on abstractions, not concretions
- [ ] Improve test coverage to validate refactoring preserves behavior
- [ ] Establish clear module boundaries and package structure

### Out of Scope

- Adding new automation features — this is a refactoring effort, not feature work
- Changing the OmniParser library (embedded third-party code) — refactoring applies to project code only
- Cross-platform support — remains Windows-only
- UI/frontend — this is a CLI/library project

## Context

The codebase has grown organically with several architectural concerns:

**SRP violations:** Executor classes mix timeline parsing, element lookup, action execution, and CLI handling. `executor.py` parses JSON, normalizes actions, and executes them all in one class. `accessibility_tree_executor.py` combines element finding, action performing, and an interactive REPL.

**No abstractions/interfaces:** Executor variants (`NaiveExecutor`, `Executor`, `TimelineAxExecutor`, `DragDropExecutor`) share no common interface. Pipeline stages are called by convention, not contract.

**Tight coupling:** `TimelineAxExecutor` directly instantiates `pyautogui` and `uiautomation` calls. Pipeline stages in `main.py` are wired by import, not injection.

**Mixed concerns in action_classes.py:** Level0 and Level1 bundle many action types into two large classes rather than composable action strategies.

**Tech stack:** Python 3.12+, PyAutoGUI, pynput, pywinauto, uiautomation, PyTorch, transformers, OmniParser (embedded), uv package manager.

## Constraints

- **Backward compatibility**: All existing functionality must continue to work after refactoring
- **Python idioms**: Use ABC, Protocol, dataclasses — not Java-style patterns forced onto Python
- **Incremental**: Refactor in safe, testable increments — never break the working system
- **Test-driven**: Each refactoring step must be validated by tests before proceeding

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Use Python Protocol/ABC for interfaces | Pythonic approach to ISP and DIP, avoids Java-isms | — Pending |
| Preserve all existing public APIs | Ensures backward compatibility during refactoring | — Pending |
| Refactor incrementally by principle | Safer than big-bang rewrite, easier to validate | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-10-14 after initialization*
