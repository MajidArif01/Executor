# Phase 1: Safety Net and Hygiene - Pattern Map

**Mapped:** 2026-10-14
**Files analyzed:** 14 new/modified (plus git hygiene operations)
**Analogs found:** 12 / 14 (the 2 without analogs are config/gitattributes, covered by RESEARCH.md)

Phase 1 adds tests and config only. No production code is restructured. Analogs are the 3 existing `unittest.TestCase` files plus the production seams the new tests drive.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `pyproject.toml` (modify: dev group + `[tool.pytest.ini_options]`) | config | n/a | existing `[dependency-groups] dev` block, `pyproject.toml` lines 31-34 | exact (same file) |
| `.gitignore` (modify), `.gitattributes` (new) | config | n/a | existing `.gitignore` | role-match |
| `tests/conftest.py` | test (fixtures) | file-I/O (load sample JSON) | RESEARCH.md "Code Examples" (no conftest exists) | no analog |
| `tests/fakes.py` | test (fake) | event-driven recorder at the backend seam | inline `Backend` class in `tests/test_scrolling.py` lines 60-66 | role-match |
| `tests/test_import_smoke.py` | test | request-response (subprocess import) | none; module list from RESEARCH.md Import Probe | no analog |
| `tests/test_normalize_timeline.py` | test (snapshot) | transform | `executor.normalize_timeline` (line 143); style from `tests/test_scrolling.py` | role-match |
| `tests/test_naive_executor_calls.py` | test | request-response + recording | `tests/test_scrolling.py::test_wheel_backend_receives_five_notches` | role-match |
| `tests/test_action_validators.py` | test | transform (pure validators) | `tests/test_typing_element.py` | role-match |
| `tests/test_mousemapper_golden.py` | test (snapshot) | transform | `tests/test_drag_elements.py` (synthetic tree builder helper) | role-match |
| `tests/test_node_golden.py` | test (snapshot) | transform | same | role-match |
| `tests/test_click_content_golden.py` | test (snapshot) | transform | same | role-match |
| `tests/test_json_roundtrip.py` | test (hand-written golden) | file-I/O | `tests/test_scrolling.py::test_timestamp_only_scroll_enrichment` (tmp dir + json write) | role-match |
| Guards in `ax_tree_graph*.py`, `automate_ax_click_matcher.py`, `copy_accessibility_json.py`, `resolve_click_elements.py` | production (verify only) | n/a | `node.py` line 887, `mousemapper.py` line 681, `click_content.py` line 604 | exact (already present) |
| Deletions/dedupe (`DataMapper.py/`, scratch files) | git hygiene | n/a | RESEARCH.md Repo Audit | n/a |

## Pattern Assignments

### Test style for all new test files (applies to every `tests/test_*.py`)

**Analog:** `tests/test_typing_element.py` and `tests/test_scrolling.py`

Conventions to copy: flat `tests/` dir, plain imports from repo root (no package prefix), small module-level builder helpers (`edit()`, `capture()`, `box()`) producing synthetic in-memory data, and a trailing `if __name__ == '__main__': unittest.main()`. New files may use plain pytest functions (needed for `snapshot` and `monkeypatch` fixtures); existing unittest files stay unchanged and pytest collects them.

```python
# tests/test_typing_element.py lines 1-12
import unittest
from Accesibilitytree.TypingElement import TypingFieldFinder

def edit(name, **flags):
    return {'name': name, 'control_type': 'EditControl', 'has_keyboard_focus': True, **flags}

def find(*children):
    return TypingFieldFinder({'tree': {'name': 'Window', 'control_type': 'WindowControl',
                                       'children': list(children)}}).find()
```

```python
# tests/test_scrolling.py lines 1-8 (imports from repo-root modules)
from Accesibilitytree.Scrolling import scroll_delta, find_scroll_target
from executor import _scroll_notches, SCROLL_UNITS_PER_NOTCH
from action_classes import ActionLevel0
```

Note: these tests import root modules directly, so `pythonpath = ["."]` in pytest config is required.

---

### `pyproject.toml` (config)

**Analog:** itself, lines 31-34

```toml
[dependency-groups]
dev = [
    "rapidfuzz>=3.14.6",
]
```

Extend this list (do not touch runtime `dependencies`) with `pytest`, `syrupy`, `pytest-randomly`, `pytest-mock`. Append the pytest block from RESEARCH.md (testpaths, `pythonpath=["."]`, `addopts = "-m 'not desktop'"`, `markers = ["desktop: ..."]`). Install via `uv add --dev ...` behind the human-verify checkpoint.

---

### `tests/fakes.py` (test fake, recording at the boundary)

**Analog:** inline backend in `tests/test_scrolling.py` lines 60-66

```python
def test_wheel_backend_receives_five_notches(self):
    class Backend:
        def scroll(self, value):
            self.value = value
    backend = Backend()
    ActionLevel0(pyautogui_backend=backend).scroll(0, -5 * SCROLL_UNITS_PER_NOTCH)
    self.assertEqual(backend.value, -5)
```

Generalize to a recorder. Seam facts from production code:

```python
# action_classes.py lines 33-56
def __init__(self, pyautogui_backend=None, sleep_backend=None, *, enable_failsafe=True):
    self._pyautogui = pyautogui_backend
    self._sleep = sleep_backend or time.sleep      # captured at construction
    ...
def _backend(self):
    ...
    self._pyautogui.FAILSAFE = self.enable_failsafe   # fake must allow attribute set
    return self._pyautogui
```

```python
# executor.py lines 376-380: NaiveExecutor passes only pyautogui_backend and enable_failsafe (no sleep_backend)
self.level_0 = ActionLevel0(pyautogui_backend=pyautogui_backend, enable_failsafe=enable_failsafe)
self.level_1 = ActionLevel1(self.level_0)
```

Use the `__getattr__` recorder from RESEARCH.md "Recording fake" (records `(name, args, sorted kwargs)`; define `FAILSAFE` and `size()`). Enumerate real backend method names with `grep "backend\."` on `action_classes.py` (known: `click`, `scroll`, plus move/mouseDown/press/etc.) and include the observed method-name set in the snapshot.

---

### `tests/test_naive_executor_calls.py` (test, request-response + recorded calls)

**Analog:** `executor.NaiveExecutor.__init__` / `run` (executor.py lines 347-489)

Constructor kwargs to use: `pyautogui_backend=fake, delay_after=0, target_resolution=(2560, 1600), free_time_max_wait=10.0, logger=lambda s: None`.

Sleep neutralization: `monkeypatch.setattr("time.sleep", sleeps.append)` BEFORE constructing the executor (ActionLevel0 binds `time.sleep` in `__init__`, line 41; `run` calls `time.sleep(self.delay_after)` at line 487 only when `delay_after` truthy and not dry run).

Dry-run behavior (important for D-08), executor.py lines 471-477:

```python
self._log(f"[{index}/{total}] {label} {self._describe(action)}".rstrip())
if not self.dry_run:
    handler(action)
results.append(ActionResult(index, action_type, ok=True))
```

Dry run only produces log lines and `ActionResult`s; no backend calls. For dry-run snapshots use `NaiveExecutor(dry_run=True, logger=lines.append, target_resolution=(2560,1600), delay_after=0)` and snapshot `lines` plus results. Real ordered calls come from the non-dry-run recording test. Drive via `ex.run(ex._load_actions(raw))` (lines 412-431) so `pyautogui.size()` is never called.

---

### `tests/test_normalize_timeline.py` (snapshot, transform)

**Analog:** `executor.normalize_timeline` signature (line 143) and RESEARCH.md example

```python
actions = normalize_timeline(raw, (2560, 1600), scroll_units_per_notch=1,
                             coalesce=False, typing_interval=0.02,
                             free_time_max=10.0, log=logs.append)
assert actions == snapshot
```

Parametrize over the 4 samples with the `sample` fixture. Snapshot derived outputs only (not raw sample JSON, 520 KB). Also cover `coalesce=True` since the working-tree executor.py now requires same `screen` for coalescing (behavior change that Phase 1 pins).

---

### `tests/test_action_validators.py` (pure validators)

**Analog:** `action_classes.py` lines 58-87, `tests/test_typing_element.py` `assertRaises` style

```python
# action_classes.py
@staticmethod
def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    return value
@classmethod
def _duration(cls, value): ... raise ValueError("duration cannot be negative.")
@classmethod
def _button(cls, value): ... casefold(); must be in {"left","middle","right"} else ValueError
@staticmethod
def _key(value): ... non-empty string else ValueError
```

Test shape (copy from `test_missing_tree_raises`):

```python
with self.assertRaises(ValueError):
    TypingFieldFinder({'ax_tree_available': False, 'tree': None})
```

Cover: bool rejected, NaN/inf rejected, negative duration, button case-folding (`" LEFT "` -> `"left"`), unsupported button, empty key.

---

### `tests/test_mousemapper_golden.py` (snapshot, transform, synthetic records)

**Analog:** `mousemapper.py` pure functions lines 216-322; helper-builder style from `tests/test_drag_elements.py` lines 1-20 (`box()` builder plus a module-level `SNAPSHOT` constant)

Targets: `bbox_of`, `bbox_area`, `bbox_pixels`, `contains`, `edge_distance_px`, `find_element_at_point(x, y, width, height, elements, nearest_radius_px=NEAREST_RADIUS_PX)` returning `(entry_or_None, status)` with `STATUS_BBOX` / `STATUS_TOLERANT` / `STATUS_NOT_FOUND`.

Synthetic element shape (what `_element_entry` reads, lines 256-267): `{"icon 0": {"type", "bbox": [x1,y1,x2,y2] normalized, "interactivity", "content", "source"}}`. Cases to pin: smallest-containing-bbox wins; tie goes to interactive element; nearest-edge fallback within radius; empty region returns `(None, STATUS_NOT_FOUND)`; non-dict element skipped; malformed bbox returns None.

Do not call `enrich_episode` on `sample/`; if tested, use `tmp_path` (it writes a `.bak` and mutates in place). `import mousemapper` pulls torch (about 4 s); import once at module level.

---

### `tests/test_node_golden.py` (snapshot, transform)

**Analog:** `node.py` lines 216-330

Targets: `normalize` (lowercase, strips `.'\`,"!?:;|`, collapses whitespace), `score_element`, `score_with_lag`, `top_row`, `anchor_distance`, `rank_elements(value, elements, anchor=None) -> (normalized, ranked)`, `grade`, `click_anchor(item)`, `field_context(items)`, `is_pending(item)`.

```python
# node.py lines 302-330: ranked entry shape and sort key
results.sort(key=lambda r: (-r["score"], not r["interactivity"], tie_break(r)))
return target, results
```

Only elements whose `source` is in `OCR_SOURCES` are ranked; include non-OCR elements in the fixture to pin the filter. Scores are rounded to 4 places, so snapshot values are stable. Use real sample items for `field_context`, `click_anchor`, `is_pending`.

---

### `tests/test_click_content_golden.py`

**Analog:** `click_content.py` lines 84-405. Pure helpers: `has_no_content`, `match_status`, `click_point`, `typing_anchor`, `typed_value`, `matched_content`, `node_record`; and `collect(..., dry_run=True)` on real sample timelines (nodes without screenshots become `status: skipped`, deterministic, worth pinning). Read the exact `collect` signature (line 288) before writing the test; it was not extracted here.

---

### `tests/test_json_roundtrip.py` (D-05, hand-written, no syrupy)

**Analog:** `tests/test_scrolling.py` lines 50-58 (tmp dir + `json.dumps` write + read back)

```python
with TemporaryDirectory(dir=Path(__file__).parent) as folder:
    path = Path(folder)
    (path / '20261009_104121_262355_accessibility.json').write_text(json.dumps(capture()))
```

Prefer pytest `tmp_path` over `TemporaryDirectory(dir=Path(__file__).parent)` so nothing is created inside `tests/`. Assert `json.loads(text)` equals the reserialized-then-reloaded structure for each sample; if testing `node.write_json_atomic(payload, path)` (line 590), write to `tmp_path` only, never `sample/`.

---

### `tests/test_import_smoke.py`

**Analog:** none in repo. Follow RESEARCH.md "Smoke test design": explicit module list, one `subprocess.run([sys.executable, "-c", "import <mod>"], cwd=ROOT)` per parametrized module, assert returncode 0 and that `pyautogui`/`pynput` are absent from `sys.modules` afterwards. Use `pytest.param(..., marks=pytest.mark.xfail(strict=True, reason=...))` for `automate_ax_click_matcher` (missing `Ax_click_matcher`) and `Data_ingetion.keyboard_data` (bare `from data_ingestion import ...`). Do not assert absence of torch (deferred to Phase 2). Scripts-with-guards verification (HYGN-03): `ax_tree_graph`, `ax_tree_graph_highlight`, `copy_accessibility_json`, `resolve_click_elements` import cleanly today.

Guard idiom already used (copy if any guard turns out to be missing):

```python
# node.py line 887 / mousemapper.py line 681 / click_content.py line 604
if __name__ == "__main__":
    ...
```

Entry-point convention: each script exposes `main(argv: list[str] | None = None)` (node.py line 854, click_content.py line 552, mousemapper.py line 653).

---

### `tests/conftest.py`

**Analog:** none. Use the RESEARCH.md fixture (`ROOT = Path(__file__).resolve().parent.parent`, params over the 4 sample names, `json.loads(... .read_text("utf-8"))`). Add a `no_sleep` fixture applying `monkeypatch.setattr("time.sleep", recorder)`. Samples are read-only inputs.

## Shared Patterns

### Dependency seam via constructor injection
**Source:** `action_classes.py` lines 33-56, `executor.py` lines 347-380
**Apply to:** `fakes.py`, `test_naive_executor_calls.py`. Inject `pyautogui_backend` (and `sleep_backend` when constructing `ActionLevel0` directly); never patch pyautogui internals (D-09).

### Fixed resolution, no desktop access
**Source:** `executor.py` lines 412-441
**Apply to:** every test touching `NaiveExecutor` or `normalize_timeline`. Always pass `target_resolution=(2560, 1600)`; otherwise `_detect_resolution()` imports pyautogui and calls `size()`.

### Synthetic in-memory data, mutation-safe
**Source:** `tests/test_drag_elements.py`, `tests/test_scrolling.py`
**Apply to:** all mapper tests. Build small dict fixtures via helper functions; use `tmp_path` for anything that writes (`enrich_episode` creates a `.bak` and rewrites in place).

### Exact-value assertions
**Source:** `tests/test_scrolling.py` lines 21-27 (`assertEqual(_scroll_notches(node), (0, -5))`)
**Apply to:** golden tests. Pin full structures (snapshot) rather than truthiness.

### Git hygiene order
**Source:** RESEARCH.md Repo Audit. Commit working tree as-is first (executor.py scroll change, untracked executors, tests, deps), then delete confirmed scratch files, then remove `DataMapper.py/mousemapper.py` and resolve the directory, then write tests. Never mix a move and a content change in one commit. Add `.pytest_cache/` and `.cursor/` to `.gitignore` (current file ends at `debug.ipynb`, `wget-log`); do not ignore `tests/__snapshots__/`.

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `tests/conftest.py` | test fixtures | file-I/O | No conftest or pytest fixtures exist; existing tests are pure unittest |
| `tests/test_import_smoke.py` | test | subprocess import | No import or subprocess tests exist |
| `.gitattributes` | config | n/a | Does not exist; use `tests/__snapshots__/** text eol=lf` |
| `tests/__snapshots__/` | snapshot store | n/a | Generated by syrupy (`--snapshot-update`), committed |

## Metadata

**Analog search scope:** `tests/`, `executor.py`, `action_classes.py`, `mousemapper.py`, `node.py`, `click_content.py`, `pyproject.toml`, `.gitignore`
**Files read:** 8 (plus CONTEXT.md and RESEARCH.md)
**Not re-read:** `click_content.collect` body, `normalize_timeline` body (planner should read before writing tests)
**Pattern extraction date:** 2026-10-14
