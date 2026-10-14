# Phase 1: Safety Net and Hygiene - Research

**Researched:** 2026-10-14
**Domain:** Python characterization testing (pytest + syrupy), git working-tree hygiene, import safety
**Confidence:** HIGH on repo facts (probed directly); MEDIUM on syrupy/pytest config details (not re-verified in docs this session)

## Summary

The phase is mostly test scaffolding plus a one-time cleanup. Probing the repo showed three findings that change the plan relative to CONTEXT.md. (1) All five scripts named in D-10 already have `if __name__ == "__main__":` guards and import cleanly, so HYGN-03 is "verify and lock in with the smoke test", not "add guards". (2) Two modules fail to import today for reasons unrelated to guards: `automate_ax_click_matcher.py` imports `Ax_click_matcher`, which exists nowhere in the tree or in git history, and `Data_ingetion/keyboard_data.py` does `from data_ingestion import DataIngestion`, a bare (non-package) import that only works when run from inside that directory. (3) `import mousemapper` (also `click_content`, via `Omniparser_Runner.config -> omniparser.parse`) pulls in torch, transformers, easyocr and paddleocr (about 4 s). No model is loaded, but "no heavy imports" cannot be asserted yet; that is deferred to Phase 2 per D-12.

The sample JSONs are already-enriched timelines (they contain `omniparser` blocks with absolute `D:\My Desktop\...` paths). `AnotatedData/` is empty, so `mousemapper.enrich_episode` and `node.enrich_episode` cannot run end to end on the samples. Golden tests for those modules must target pure functions with synthetic OmniParser records. Run `normalize_timeline` and `NaiveExecutor` on the real samples.

The `.venv` is Python 3.12.10 and has no pytest. The 3 existing test files are `unittest.TestCase` and pass (15 tests) under `python -m unittest discover tests`; pytest collects them unchanged.

**Primary recommendation:** Wave order: (1) commit working-tree state exactly as-is (pins current behavior), then dedupe and rename in separate commits; (2) pytest config plus dev deps; (3) smoke test and `__main__` verification; (4) pure-function snapshots; (5) recording-fake executor tests. Never mix a move and a content change in one commit.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Audit every duplicate pair (`mousemapper.py` vs `DataMapper.py/mousemapper.py`, deleted files in git status) and pick the canonical copy per file. Delete the other. Commit the cleanup before any test writing.
- **D-02:** Rename `DataMapper.py/` to a valid Python package name (e.g., `data_mapper/` or merge its contents into the canonical location). The current name with `.py` extension causes import confusion.
- **D-03:** Resolve all pending git deletions (`find_element.py`, `find_ui_element.py`, `automation_script.py`, `chatgpt_demo.py`, `Keyboard_Mapping.py`) — confirm which are intentional and commit.
- **D-04:** Use syrupy (6.1.1) for snapshot/characterization tests of normalizer and mapper outputs. Snapshot the exact JSON structure that `normalize_timeline`, `mousemapper`, `node.py`, and `click_content` produce when run against the 4 sample JSONs (`Calculator.json`, `Daraz_Payment.json`, `Daraz_Searching.json`, `bluetooth_setting.json`).
- **D-05:** Use hand-written golden-file assertions for JSON round-trip integrity (ensure loading and re-serializing produces identical output).
- **D-06:** Configure pytest with a `desktop` marker deselected by default (`-m "not desktop"` in `pyproject.toml`). Live-desktop tests are opt-in only.
- **D-07:** Build recording fakes that capture the ordered list of action calls an executor makes (click at (x,y), scroll down 3, type "hello", etc.). Assert the exact sequence against known timelines.
- **D-08:** Use `NaiveExecutor` dry-run mode to snapshot expected action sequences for each sample timeline.
- **D-09:** No mocking of pyautogui/uiautomation internals at this stage — fakes record at the boundary, not deep inside.
- **D-10:** Add `if __name__ == "__main__":` guards to all scripts with import-time side effects: `ax_tree_graph.py`, `ax_tree_graph_highlight.py`, `automate_ax_click_matcher.py`, `copy_accessibility_json.py`, `resolve_click_elements.py`.
- **D-11:** Create an import smoke test that imports every Python module in the project and asserts no exceptions, no GUI popups, and no heavy model loading at import time.
- **D-12:** Defer lazy torch/model imports to Phase 2 (ports and adapters) — only add `__main__` guards in Phase 1.

### Claude's Discretion
- Test file organization (flat in `tests/` vs subdirectories) — use whatever pytest discovers cleanly
- Exact syrupy serializer configuration — use defaults unless sample data requires custom serialization
- Whether to add `pytest-randomly` and `pytest-mock` now or defer — add them now since they're cheap

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SAFE-01 | Characterization tests for timeline normalizer, NaiveExecutor dry-run, action validators | `normalize_timeline(raw, target_resolution, *, scroll_units_per_notch, coalesce, typing_interval, free_time_max, log)` is pure; pass a fixed `target_resolution=(2560,1600)` so `pyautogui.size()` is never called. Validators = `ActionLevel0._number/_button/_key/_duration` (static/classmethods, pure). |
| SAFE-02 | Golden tests for mousemapper, node.py, click_content | Pure fns: `mousemapper.find_element_at_point`, `bbox_of`, `contains`, `edge_distance_px`; `node.rank_elements`, `score_element`, `score_with_lag`, `normalize`, `field_context`; `click_content.collect(..., dry_run=True)`. Samples lack OmniParser record files, so records are synthetic (see Pitfall 2). |
| SAFE-03 | Recording fakes assert exact ordered action calls | `NaiveExecutor(pyautogui_backend=FakeBackend)` and `ActionLevel0(pyautogui_backend=..., sleep_backend=...)` already accept injection. |
| SAFE-04 | Import smoke test of every legacy module | Probe results in "Import Probe" below; 2 modules need a decision. |
| SAFE-05 | pytest `desktop` marker deselected by default | `[tool.pytest.ini_options]` with `markers` and `addopts`. |
| HYGN-01 | Canonical copy per duplicate | Audit table below. |
| HYGN-02 | Rename `DataMapper.py/` | Contents are a stale duplicate plus one stray JSON; recommend dissolving the directory (see Open Questions). |
| HYGN-03 | `__main__` guards | Already present on all 5; verify via smoke test. |
</phase_requirements>

## Architectural Responsibility Map

Not applicable in the web-tier sense. This is a single-process Windows desktop tool. Test code lives in `tests/`; fakes live in `tests/fakes.py` (or `tests/conftest.py`), never in production modules.

## Standard Stack

All dev-only, added to `[dependency-groups] dev` (not runtime deps; `numpy==1.26.4`, `transformers==4.46.3` pins constrain resolution).

| Library | Version | Purpose |
|---------|---------|---------|
| pytest | 9.1.1 | runner [VERIFIED: pypi.org JSON API, 2026-10-14] |
| syrupy | 6.1.1 | snapshot tests, per D-04 [VERIFIED: PyPI] |
| pytest-randomly | 5.0.0 | order shuffling [VERIFIED: PyPI] |
| pytest-mock | 3.16.0 | `mocker`/monkeypatch helper [VERIFIED: PyPI] |
| pytest-cov | 7.1.0 | optional; branch coverage later [VERIFIED: PyPI] |

ruff 0.17.0 and mypy 2.4.0 are Phase 5 (STAT-01/02); do not configure them in Phase 1 beyond adding to dev group if desired.

All require Python >=3.10, compatible with the `.venv` 3.12.10 [VERIFIED: PyPI + `.venv/Scripts/python.exe --version`].

**Installation:** `uv add --dev pytest syrupy pytest-randomly pytest-mock` (then `uv run pytest`). Note the machine's system `python` is 3.14.5 but the project venv is 3.12; always use `uv run` or `.venv\Scripts\python.exe`.

## Package Legitimacy Audit

slopcheck was not run (not installed in this session). Names come from the project's own STACK.md research and are all long-established, mainstream pytest ecosystem packages; existence and latest versions were confirmed against the PyPI JSON API. Per the provenance rule they are tagged `[ASSUMED]` until the planner gates the install.

| Package | Registry | Source Repo | slopcheck | Disposition |
|---------|----------|-------------|-----------|-------------|
| pytest, syrupy, pytest-randomly, pytest-mock, pytest-cov | PyPI | well-known (pytest-dev, syrupy-project, etc.) | not run | Approved, plan should add a `checkpoint:human-verify` before `uv add` |

**Removed [SLOP]:** none. **Flagged [SUS]:** none known.

## Architecture Patterns

### Flow of the safety net
```
sample/*.json --> normalize_timeline(fixed resolution) --> actions list --> syrupy snapshot
                                   |
                                   +--> NaiveExecutor(pyautogui_backend=RecordingFake, no sleep) --> ordered call list --> snapshot
synthetic OmniParser records --> mousemapper/node pure fns --> snapshot
sample items --> click_content.collect(dry_run=True) / node.field_context --> snapshot
every module name --> importlib.import_module (subprocess or sys.modules diff) --> pass / known-broken list
```

### Recommended test layout
```
tests/
├── conftest.py              # sample loader fixtures, no-sleep fixture, ROOT on path
├── fakes.py                 # RecordingPyAutoGui (records calls as tuples)
├── test_import_smoke.py
├── test_normalize_timeline.py     # snapshot x 4 samples
├── test_naive_executor_calls.py   # recording fake x 4 samples + dry-run
├── test_action_validators.py
├── test_mousemapper_golden.py
├── test_node_golden.py
├── test_click_content_golden.py
├── test_json_roundtrip.py   # D-05 hand-written, no syrupy
├── __snapshots__/           # committed
└── (existing 3 files, unchanged)
```

### pytest configuration (pyproject.toml)
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]          # tests do `from executor import ...`; modules live at repo root
addopts = "-m 'not desktop'"
markers = ["desktop: needs a live Windows desktop (opt-in: pytest -m desktop)"]
```
`pythonpath` is a built-in pytest option (>=7). Run desktop tests with `pytest -m desktop` (a later `-m` overrides the one in addopts). [ASSUMED: from pytest docs knowledge; confirm override behavior in a Wave 0 check by running `pytest -m desktop --collect-only`.]

### Recording fake (boundary, per D-09)
Inject at `pyautogui_backend`, the existing seam. A minimal fake records `(method, args, kwargs)` for `click, moveTo, mouseDown, mouseUp, press, keyDown, keyUp, scroll, hscroll, typewrite/write, hotkey, dragTo` (check exact methods used in `action_classes.py` lines 22-370 when writing; grep `backend\.` to enumerate), plus attributes `FAILSAFE`, and `size()`. Because `ActionLevel0` sets `backend.FAILSAFE`, the fake must allow attribute assignment.

Sleep must be neutralized: `ActionLevel0.__init__` binds `self._sleep = sleep_backend or time.sleep` and `NaiveExecutor` does not pass `sleep_backend`; `NaiveExecutor.run` also calls `time.sleep(self.delay_after)`. In tests use `delay_after=0`, `monkeypatch.setattr("executor.time.sleep", ...)` before constructing, and/or set `executor.level_0._sleep = recorder` after construction. Record sleep calls too (timing metadata per CONTEXT specifics).

### Dry-run subtlety (D-08)
In `NaiveExecutor.run`, `dry_run=True` logs and never calls the handler, so dry-run yields only log lines and `ActionResult`s, not backend calls. The two deterministic artifacts are: (a) `normalize_timeline` output (the action list), and (b) the `logger` lines from a dry run (`NaiveExecutor(dry_run=True, logger=lines.append, target_resolution=(2560,1600), delay_after=0)`). Snapshot both. The recording-fake test (non-dry-run) provides the true ordered backend calls. Pass `target_resolution` explicitly or `_detect_resolution()` calls `pyautogui.size()`.

### Anti-Patterns
- Snapshotting the raw sample JSON (huge, 520 KB). Snapshot derived outputs only.
- Snapshotting values containing absolute paths from samples (`D:\My Desktop\...`); they are stable but machine-irrelevant; fine as inputs, avoid in outputs where possible.
- Mocking `pyautogui` module internals (violates D-09).

## Don't Hand-Roll

| Problem | Use Instead |
|---------|-------------|
| Snapshot storage/diff/update | syrupy (`--snapshot-update`); for large JSON-like outputs consider its `JSONSnapshotExtension` (one `.json` per test, readable git diffs) [ASSUMED] |
| Temp episode directories | pytest `tmp_path` |
| Patching sleep/time | pytest `monkeypatch` |
| Import-everything discovery | `pkgutil.walk_packages` / explicit module list; do not hand-maintain unless using the explicit list below |

## Runtime State Inventory

Not a rename phase, but the DataMapper rename/dedupe touches paths:
| Category | Finding | Action |
|----------|---------|--------|
| Stored data | None. `DataMapper.py/20260928_112828_967515_accessibility.json` (409 KB, untracked) is a captured AX tree, not referenced by code (grep found no references to `DataMapper` in any .py/.md/.toml outside `.venv`/`omniparser`/`.planning`) | decide: move to `tests/fixtures/` or delete |
| Live service config / OS-registered state / Secrets | None found | none |
| Build artifacts | `__pycache__/` dirs (gitignored) | none |

## Repo Audit (HYGN-01/02/03)

Git status at research time and recommended disposition:

| Item | State | Evidence | Recommendation |
|------|-------|----------|----------------|
| `mousemapper.py` (root, 682 lines) vs `DataMapper.py/mousemapper.py` (6.6 KB) | both tracked | root is imported by `main.py` (stage `"mousemapper"`) and `click_content.py` (`from mousemapper import NEAREST_RADIUS_PX`); the DataMapper copy is an older Sep 3 version with different content | root canonical; `git rm DataMapper.py/mousemapper.py` |
| `DataMapper.py/` directory | contains only the stale copy and an untracked 409 KB json | no code references it | dissolve (see Open Question 1) |
| deleted `Accesibilitytree/find_element.py` | no references anywhere | superseded by `Accesibilitytree/ClickElement.py` etc. | commit deletion |
| deleted `find_ui_element.py`, `automation_script.py`, `chatgpt_demo.py`, `KeyboardExtraction/Keyboard_Mapping.py`, `test.png`, `AnotatedData/.../20260909_..._accessibility.json` | no references | scratch/demo files | commit deletions (user to confirm per D-03) |
| modified `executor.py` | **behavior change**: `SCROLL_UNITS_PER_NOTCH` 120 to 1, `_scroll_notches` now delegates to `Accesibilitytree.Scrolling.scroll_delta`, scroll coalescing now also requires same `screen` | `git diff HEAD -- executor.py` | commit as-is first; this is the "current behavior" the snapshots pin |
| modified `pyproject.toml`, `uv.lock` | +38 / +3686 lines | already carry the new deps | commit (CRLF warnings only) |
| untracked `accessibility_tree_executor.py`, `timeline_ax_executor.py`, `drag_drop_executor.py`, `ax_tree_graph*.py`, `automate_ax_click_matcher.py` | real code; `timeline_ax_executor` imports `accessibility_tree_executor` | must be committed before refactoring | `git add` |
| untracked `tests/` | the 3 passing test files | | commit |
| `.cursor/`, `crop_review_1.png`, `crop_review_9.png` | editor/scratch | | add `.cursor/` to `.gitignore` (or commit rules if wanted); delete or ignore the pngs |
| `.gitignore` | lacks `.pytest_cache/`, `.venv` present | | add `.pytest_cache/`; do NOT ignore `tests/__snapshots__/` |

Suggested commit sequence: (1) commit current state unchanged (executor.py edits, untracked executors, tests, deps); (2) delete confirmed scratch files; (3) remove duplicate mousemapper and resolve `DataMapper.py/`; (4) then tests. Rule from STATE.md: move first, restructure second.

## Import Probe (SAFE-04, measured with `.venv` Python 3.12.10)

Probed 36 modules via `importlib.import_module` from the repo root.
- OK, no heavy deps: `action_classes, executor, timeline_ax_executor, drag_drop_executor, node, click_content, DataParser, copy_accessibility_json, resolve_click_elements, ax_tree_graph, ax_tree_graph_highlight, main`, all `Accesibilitytree.*`, `Data_parser.*`, `Omniparser_Runner.*`.
- `accessibility_tree_executor` imports `uiautomation` at module level (0.11 s, no GUI action). Acceptable but note for Phase 2 (PORT-05).
- `mousemapper` took 4.2 s and loaded `torch`, `transformers`, `easyocr`, `paddleocr` (via `Omniparser_Runner.config` -> `omniparser.parse`). Nothing is instantiated, no model weights loaded. Because `node.py` and `click_content.py` import the same config, importing any of them in one process pays this once.
- FAIL `automate_ax_click_matcher`: `ModuleNotFoundError: Ax_click_matcher` (file does not exist in tree or git history; `match_click` is defined nowhere).
- FAIL `Data_ingetion.keyboard_data`: `from data_ingestion import DataIngestion` (needs `Data_ingetion/` on sys.path; package has no `__init__.py`).
- `omniparser/` is third-party and out of scope; do not smoke-test it. Warning from `requests` about urllib3/chardet versions is noise.

**Smoke test design:** explicit module list (not discovery, so omniparser is excluded), each import run in a subprocess (`sys.executable -I`-style with cwd=repo root, `PYTHONPATH` set) so one failure or torch load cannot leak between tests and `pytest-randomly` order does not matter; parametrize one test per module. Assert returncode 0, and assert `pyautogui`/`pynput` not in `sys.modules` afterwards (those are the GUI-side-effect libs; both pass today). For the 2 broken modules: do not silently skip; use `pytest.param(..., marks=pytest.mark.xfail(strict=True, reason=...))` so a later fix flips the test and forces removal of the marker. Do not assert absence of torch (D-12 defers it); add a documented `xfail`/TODO test for "no torch at import" to be turned on in Phase 2 [recommendation].

## Common Pitfalls

### Pitfall 1: Dry-run does not exercise handlers
`dry_run` skips handlers; snapshot of "action sequence" must come from `normalize_timeline` and logs, with real call order from the recording fake. See Architecture Patterns.

### Pitfall 2: Samples cannot drive mousemapper/node end to end
`enrich_episode` requires `AnotatedData/AnotatedJson/{mouse,scroll}/node_<i>_<stem>.json` OmniParser records plus screenshots; `AnotatedData/` is empty and samples reference another machine's paths. Build synthetic records (small `elements` dict of `icon N -> {type, bbox (normalized), interactivity, content, source}`), either hand-written or generated from each sample's existing `omniparser.matched_element`. Use `tmp_path` episode dirs for any `enrich_episode` test (it writes a `.bak` backup and mutates the timeline in place; never point it at `sample/`). `click_content.collect(..., dry_run=True)` on sample timelines needs no images (nodes without screenshots become `status: skipped`, which is itself deterministic and worth pinning). Use real sample items for pure functions (`field_context`, `click_anchor`, `is_pending`). This is a deviation from D-04's wording "run against the 4 sample JSONs" for the mappers and should be stated in the plan.

### Pitfall 3: Real sleeps
`wait` actions (up to `FREE_TIME_MAX_WAIT=10`), `move(duration=0.2)` inside every click, and `delay_after=2.0` default would make tests take minutes. Neutralize sleep (see fake section). `ActionLevel0.click` calls `self.move(..., duration=0.2)` first, so the recorded sequence for one click is a move plus a click; assert that exactly.

### Pitfall 4: Platform and environment dependence of snapshots
`TARGET_RESOLUTION=None` auto-detects the screen; always pass an explicit resolution. Snapshots must not contain timestamps from `datetime.now()` (`backup_timeline` writes UTC-timestamped `.bak`; normalize or don't snapshot that name). Windows CRLF: the repo shows autocrlf warnings, so add `.gitattributes` with `tests/__snapshots__/** text eol=lf` (or binary) to avoid snapshot diffs by line ending [ASSUMED].

### Pitfall 5: pytest-randomly and module globals
`mousemapper` and `executor` modify `sys.path` at import; random order plus `pythonpath=["."]` is fine, but any test that monkeypatches module constants must use `monkeypatch` so it is undone. Randomly also reseeds `random`; no project code is known to use it. If a flaky order dependency appears, that is a finding to record, not suppress.

### Pitfall 6: Snapshot of the unrefactored behavior includes known oddities
`executor.py` header has garbled comments (`# noqa: E402rones`, `3figh--m---`) and the `SCROLL_UNITS_PER_NOTCH = 1` comment contradicts the next two comment lines. These are not behavior; do not "fix" in Phase 1 (content changes belong after the safety net).

### Pitfall 7: Snapshot churn masking regressions
Use `--snapshot-update` only deliberately; in CI-like runs use `--snapshot-warn-unused` and review diffs. Commit `__snapshots__`.

## Code Examples

```python
# tests/conftest.py -- sample loader (stdlib only)
import json, pathlib, pytest
ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ["Calculator", "Daraz_Payment", "Daraz_Searching", "bluetooth_setting"]

@pytest.fixture(params=SAMPLES)
def sample(request):
    return request.param, json.loads((ROOT / "sample" / f"{request.param}.json").read_text("utf-8"))
```

```python
# normalizer characterization (syrupy injects `snapshot`)
from executor import normalize_timeline

def test_normalize(sample, snapshot):
    name, raw = sample
    logs = []
    actions = normalize_timeline(raw, (2560, 1600), scroll_units_per_notch=1,
                                 coalesce=False, typing_interval=0.02,
                                 free_time_max=10.0, log=logs.append)
    assert actions == snapshot            # also snapshot `logs` in a second assertion/test
```

```python
# recording fake at the existing seam
class RecordingPyAutoGui:
    FAILSAFE = True
    def __init__(self): self.calls = []
    def __getattr__(self, name):               # click, moveTo, scroll, press, write, ...
        def rec(*a, **k): self.calls.append((name, a, tuple(sorted(k.items()))))
        return rec
    def size(self): return (2560, 1600)
```
Caveat: `__getattr__` fakes accept any method name, so a typo in production code is silently recorded; that is acceptable for characterization (it pins what is called today), but list the observed method names in the snapshot so drift is visible.

```python
# executor with fake, no real time
def test_calc_calls(sample, snapshot, monkeypatch):
    name, raw = sample
    fake = RecordingPyAutoGui(); sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)   # covers executor and action_classes (both use time.sleep)
    ex = NaiveExecutor(pyautogui_backend=fake, delay_after=0, target_resolution=(2560, 1600),
                       free_time_max_wait=10.0, logger=lambda s: None)
    ex.run(ex._load_actions(raw))
    assert (fake.calls, sleeps) == snapshot
```
`monkeypatch.setattr("time.sleep", ...)` patches the shared `time` module attribute, so it works for both modules as long as it is applied before `ActionLevel0.__init__` runs (it captures `time.sleep` at construction). [ASSUMED pattern; verify the 4 samples run without raising, since e.g. `type`/`key_press` handlers call backend methods the fake must accept.]

## State of the Art

| Old | Current | Note |
|-----|---------|------|
| `unittest` only | pytest collecting unittest classes unchanged | no rewrite of the 3 existing tests required |
| mypy/ruff/import-linter | Phase 5 | out of scope now |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Package names/versions acceptable without slopcheck | Package Legitimacy | low; mainstream packages, human-verify gate suggested |
| A2 | `pytest -m desktop` overrides `addopts -m 'not desktop'` | pytest config | low; Wave 0 check |
| A3 | syrupy `JSONSnapshotExtension` exists with per-test JSON files | Don't Hand-Roll | low; fallback is default `.ambr` |
| A4 | `.gitattributes` needed to keep snapshots LF on Windows | Pitfall 4 | low |
| A5 | `time.sleep` monkeypatch works for both modules | Code Examples | medium; verify in Wave 0 |
| A6 | `__getattr__` fake covers every backend method the 4 samples hit | Code Examples | low; run once to see |

## Open Questions

1. **Is `DataMapper.py/` intentional (STATE.md blocker)?**
   - Known: only an older duplicate `mousemapper.py` plus a stray AX json; nothing imports it; the name can never be a package (`DataMapper.py` is not a valid module path).
   - Recommendation: dissolve it (delete stale copy, `git mv` the json to `tests/fixtures/ax/` or delete it). HYGN-02 literally says "renamed"; if the user wants a literal rename, use `data_mapper/` with an `__init__.py` holding the json, but that creates a package with no code. Get a one-line confirmation in the plan's checkpoint.
2. **`automate_ax_click_matcher.py` depends on a nonexistent `Ax_click_matcher.py`.** Options: commit as-is and xfail (recommended; Phase 1 does no restructuring) or ask the user whether the matcher file was lost. Do not delete without confirmation.
3. **`Data_ingetion/keyboard_data.py` bare import.** Same treatment: xfail in smoke test now; fixing the import is a content change deferred to a later phase.
4. **Do any external scripts import legacy module names?** Unknown (STATE.md); not answerable from the repo.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python (.venv) | everything | yes | 3.12.10 | none needed |
| system python | n/a | yes | 3.14.5 (do not use) | use `uv run` |
| uv | dependency install | yes | 0.11.14 | pip in venv |
| pytest and plugins | tests | no (not installed in .venv) | n/a | `uv add --dev ...` (network to PyPI; reachable at research time) |
| live desktop | `desktop`-marked tests only | n/a | n/a | tests use fakes |

## Validation Architecture

Skipped: `workflow.nyquist_validation` is `false` in `.planning/config.json`. Quick check commands for the planner: `uv run pytest -x -q` (full suite is fast; expect a few seconds plus about 4 s of torch import per subprocess in the smoke test, so keep the smoke test list parametrized but consider running subprocess imports of torch-pulling modules once).

## Security Domain

`security_enforcement` is not set in config (treated as enabled), but this phase adds no network, auth, input-handling or crypto surface: tests, config and file moves only. ASVS V5 relevance is limited to not letting tests mutate `sample/` (use `tmp_path` copies) and to not executing untrusted data. Supply-chain: gate `uv add` of the 4-5 dev packages behind the legitimacy checkpoint above.

## Project Constraints (from CLAUDE.md)

- Backward compatibility: no regressions; Python idioms (Protocol/ABC/dataclasses); incremental and test-driven.
- Dev tools go in `[dependency-groups] dev`, never runtime deps.
- Tests must run without a live desktop; real-desktop tests behind `@pytest.mark.desktop`, deselected by default; use fakes (no Windows CI assumed).
- Exclude `omniparser/` from linting, typing and tests.
- Use hand-written fakes at Protocol-like seams over mocks where possible.
- GSD workflow: file edits go through a GSD command (`/gsd-execute-phase`).

## Sources

### Primary (HIGH)
- Direct repo probes: `git status`, `git diff HEAD -- executor.py`, `grep`, per-module import probe script (`importlib`, `.venv` Python 3.12.10), reading `executor.py`, `action_classes.py`, `mousemapper.py`, `node.py`, `click_content.py`, `main.py`, `pyproject.toml`, `sample/*.json`.
- PyPI JSON API (`https://pypi.org/pypi/<pkg>/json`), 2026-10-14: pytest 9.1.1, syrupy 6.1.1, pytest-randomly 5.0.0, pytest-mock 3.16.0, pytest-cov 7.1.0, ruff 0.17.0, mypy 2.4.0.
- `.venv` unittest run: 15 existing tests pass.

### Tertiary (LOW)
- pytest and syrupy configuration semantics recalled from training; not re-checked against docs in this session (Context7 not consulted). See Assumptions A2 to A5.

## Metadata

**Confidence:** repo facts HIGH; tool configuration MEDIUM; plan ordering recommendations HIGH.
**Research date:** 2026-10-14. **Valid until:** about 30 days or until the working tree changes.
