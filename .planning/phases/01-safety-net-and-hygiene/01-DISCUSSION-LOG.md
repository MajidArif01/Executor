# Phase 1: Safety Net and Hygiene - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-14
**Phase:** 1-Safety Net and Hygiene
**Areas discussed:** Duplicate resolution, Test framework, Executor testing, Import safety
**Mode:** --auto (all areas auto-selected, recommended options chosen)

---

## Duplicate Resolution

| Option | Description | Selected |
|--------|-------------|----------|
| Audit and pick canonical | Delete duplicates, commit cleanup first | ✓ |
| Keep both with aliases | Maintain backward compatibility for both | |
| Defer to later phase | Focus only on tests now | |

**User's choice:** [auto] Audit and pick canonical copy per file (recommended default)
**Notes:** Git status shows deletions and untracked files. `DataMapper.py/` directory name is invalid as a Python package.

---

## Test Framework

| Option | Description | Selected |
|--------|-------------|----------|
| syrupy snapshots + golden files | Snapshot normalizer/mapper output; golden files for JSON round-trips | ✓ |
| Hand-written assertions only | No snapshot framework, manual expected values | |
| pytest-regtest | Alternative snapshot framework | |

**User's choice:** [auto] syrupy snapshots + golden files (recommended default)
**Notes:** 4 sample JSONs available as test fixtures. Existing tests use unittest.TestCase pattern.

---

## Executor Testing

| Option | Description | Selected |
|--------|-------------|----------|
| Recording fakes | Capture ordered action calls at boundary | ✓ |
| Mock pyautogui internals | Patch internal calls with unittest.mock | |
| Dry-run output capture | Only test NaiveExecutor dry-run mode | |

**User's choice:** [auto] Recording fakes (recommended default)
**Notes:** Fakes assert exact ordered action sequences. NaiveExecutor dry-run also captured as snapshots.

---

## Import Safety

| Option | Description | Selected |
|--------|-------------|----------|
| __main__ guards on side-effect scripts | Add guards to ax_tree_graph*.py, automate_ax_click_matcher.py, etc. | ✓ |
| Lazy imports everywhere | Also make torch/model imports lazy | |
| Defer entirely | Handle in later phases | |

**User's choice:** [auto] __main__ guards on side-effect scripts (recommended default)
**Notes:** Lazy torch imports deferred to Phase 2 (ports and adapters). Import smoke test covers all modules.

---

## Claude's Discretion

- Test file organization (flat vs subdirectories)
- syrupy serializer configuration
- Whether to add pytest-randomly and pytest-mock now (decided: yes, they're cheap)

## Deferred Ideas

None — discussion stayed within phase scope
