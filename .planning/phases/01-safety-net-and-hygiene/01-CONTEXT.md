# Phase 1: Safety Net and Hygiene - Context

**Gathered:** 2026-10-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Stabilize the working tree and pin current behavior with characterization tests so SOLID refactoring can begin safely. This phase delivers: a clean git state with canonical files chosen, pytest infrastructure with snapshot/golden-file tests, recording fakes for executor testing, and import safety guards. No code is restructured — only tested and cleaned.

</domain>

<decisions>
## Implementation Decisions

### Duplicate Resolution
- **D-01:** Audit every duplicate pair (`mousemapper.py` vs `DataMapper.py/mousemapper.py`, deleted files in git status) and pick the canonical copy per file. Delete the other. Commit the cleanup before any test writing.
- **D-02:** Rename `DataMapper.py/` to a valid Python package name (e.g., `data_mapper/` or merge its contents into the canonical location). The current name with `.py` extension causes import confusion.
- **D-03:** Resolve all pending git deletions (`find_element.py`, `find_ui_element.py`, `automation_script.py`, `chatgpt_demo.py`, `Keyboard_Mapping.py`) — confirm which are intentional and commit.

### Test Framework
- **D-04:** Use syrupy (6.1.1) for snapshot/characterization tests of normalizer and mapper outputs. Snapshot the exact JSON structure that `normalize_timeline`, `mousemapper`, `node.py`, and `click_content` produce when run against the 4 sample JSONs (`Calculator.json`, `Daraz_Payment.json`, `Daraz_Searching.json`, `bluetooth_setting.json`).
- **D-05:** Use hand-written golden-file assertions for JSON round-trip integrity (ensure loading and re-serializing produces identical output).
- **D-06:** Configure pytest with a `desktop` marker deselected by default (`-m "not desktop"` in `pyproject.toml`). Live-desktop tests are opt-in only.

### Executor Testing
- **D-07:** Build recording fakes that capture the ordered list of action calls an executor makes (click at (x,y), scroll down 3, type "hello", etc.). Assert the exact sequence against known timelines.
- **D-08:** Use `NaiveExecutor` dry-run mode to snapshot expected action sequences for each sample timeline.
- **D-09:** No mocking of pyautogui/uiautomation internals at this stage — fakes record at the boundary, not deep inside.

### Import Safety
- **D-10:** Add `if __name__ == "__main__":` guards to all scripts with import-time side effects: `ax_tree_graph.py`, `ax_tree_graph_highlight.py`, `automate_ax_click_matcher.py`, `copy_accessibility_json.py`, `resolve_click_elements.py`.
- **D-11:** Create an import smoke test that imports every Python module in the project and asserts no exceptions, no GUI popups, and no heavy model loading at import time.
- **D-12:** Defer lazy torch/model imports to Phase 2 (ports and adapters) — only add `__main__` guards in Phase 1.

### Claude's Discretion
- Test file organization (flat in `tests/` vs subdirectories) — use whatever pytest discovers cleanly
- Exact syrupy serializer configuration — use defaults unless sample data requires custom serialization
- Whether to add `pytest-randomly` and `pytest-mock` now or defer — add them now since they're cheap

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Context
- `.planning/PROJECT.md` — Project goals, core value, constraints, and key decisions
- `.planning/REQUIREMENTS.md` — Full requirements with REQ-IDs (SAFE-01..05, HYGN-01..03 are this phase)
- `.planning/ROADMAP.md` — Phase 1 success criteria and dependencies

### Research
- `.planning/research/SUMMARY.md` — Synthesized research findings including stack recommendations and refactoring order
- `.planning/research/PITFALLS.md` — Critical pitfalls to avoid, especially #1 (no safety net) and #9 (leaky abstractions)
- `.planning/research/STACK.md` — Dev dependency versions and tool configurations

### Existing Tests
- `tests/test_drag_elements.py` — 44 lines, tests DragElementFinder (pattern to follow)
- `tests/test_scrolling.py` — 69 lines, tests scroll resolution (pattern to follow)
- `tests/test_typing_element.py` — 37 lines, tests TypingFieldFinder (pattern to follow)

### Sample Data
- `sample/Calculator.json` — Calculator automation timeline
- `sample/Daraz_Payment.json` — E-commerce payment timeline
- `sample/Daraz_Searching.json` — E-commerce search timeline
- `sample/bluetooth_setting.json` — Windows bluetooth settings timeline

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/test_drag_elements.py`, `test_scrolling.py`, `test_typing_element.py`: Existing unittest.TestCase tests with synthetic accessibility trees — follow this pattern for new tests
- `sample/*.json`: 4 real recorded timelines usable as golden-file test fixtures
- `executor.py` already has a `dry_run` mode that skips actual input — leverage for action sequence capture

### Established Patterns
- Tests use `unittest.TestCase` with synthetic in-memory data (no real UI needed)
- Each enrichment script has a `main(argv)` function callable in-process
- Timeline items are dicts mutated in-place with backup files created first

### Integration Points
- `main.py` orchestrates pipeline stages by calling `main(argv)` on each module
- `pyproject.toml` needs dev dependency group additions (pytest, syrupy, ruff, mypy)
- `Accesibilitytree/__init__.py` exports the public API for the AX matching subsystem

</code_context>

<specifics>
## Specific Ideas

- Snapshot the exact JSON output structure (not just pass/fail) so any behavioral drift during refactoring phases is immediately caught
- The 4 sample timelines cover different app types (Calculator, e-commerce, system settings) giving good coverage diversity
- Recording fakes should capture action type + coordinates + text + timing metadata to catch subtle ordering/timing regressions

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 1-Safety Net and Hygiene*
*Context gathered: 2026-10-14*
