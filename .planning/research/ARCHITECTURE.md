# Architecture Patterns

**Domain:** Windows UI automation record/enrich/replay system, SOLID refactor
**Researched:** 2026-10-14
**Confidence:** MEDIUM-HIGH. The existing structure comes from reading the code (executor.py, main.py, action_classes.py, timeline_ax_executor.py). The patterns are standard Python idioms (typing.Protocol, ABC, registry, dependency injection) from training knowledge. No external library docs were needed.

## Observed Current State (facts from code)

- `main.py` has a declarative `STAGES` list of dicts, each naming a module whose `main(argv)` runs it. Stages are invoked by module name with an `accepts` flag set. The contract is "has `main(argv)`", which is implicit and CLI-shaped.
- `executor.py` (657 lines) holds the timeline normalizer (`normalize_timeline`, `_scale_point`, `_freetime_seconds` and similar), `NaiveExecutor`, `ActionResult`/`ActionError`, and a CLI `main`. `NaiveExecutor` already uses a `_handlers` dict keyed on action type, so it is half-way to a registry. It still hard-wires `ActionLevel0/1`.
- `action_classes.py`: `ActionLevel0` (click, mouse_down/up, keypress, key_down/up, move, scroll, wait) and `ActionLevel1(level_0)` (type, type_text, combo_key, drag, hover, scroll_step). Validation helpers (`_number`, `_button`, `_key`) are mixed in with the pyautogui backend lookup (`_backend`).
- `timeline_ax_executor.py` imports from `executor` (the normalizer), from `accessibility_tree_executor` (`ElementQuery`, `auto`) and from `Accesibilitytree.accessibility_tree`. Its `run_item` / `click` / `scroll` / `drag` methods all take raw `dict` items.
- `drag_drop_executor.py` is procedural (module functions, `ctypes` DPI handling, its own `walk`/`resolve`). It does not use `ActionLevel*`.
- `accessibility_tree_executor.py` holds a class also named `Executor`, which collides with `executor.py`. It contains data classes (`ElementQuery`, `ElementInfo`, `WindowInfo`), the tree walker, the action performer and the REPL.
- `Accesibilitytree/` is a package. It holds ClickElement, TypingElement, Scrolling, find_drag_elements and records, and it is the best-structured area. Tests exist only for drag, scroll and typing finders.
- There are duplicate modules (`mousemapper.py` and `DataMapper.py/mousemapper.py`, `find_element.py`, `find_ui_element.py`), and git shows several files deleted. The dedupe must be settled first.

## Recommended Architecture

Use a layered, ports-and-adapters layout. Dependencies point inward only. Python `Protocol` is used for ports that have several implementations or that need test fakes. `ABC` is used only where shared template behaviour exists.

```
                       CLI layer (thin argparse shims; keep old entry points)
                                  |
                      Application layer
        +-------------------------+--------------------------+
        |  PipelineRunner         |  ReplayEngine            |
        |  (stages)               |  (loop, error policy)    |
        +------------+------------+-------------+------------+
                     |                          |
        Domain: Timeline model       Domain: Action model + ActionRegistry
        (TimelineItem, Action,       (Action handlers, strategy per type)
         Resolution, ElementRef)                |
                     |                          |
        +------------+-----------+   +----------+-------------------+
        | Ports (Protocols):     |   | Ports (Protocols):           |
        | Stage, TimelineLoader, |   | InputBackend, Clock,         |
        | ElementLocator,        |   | ElementLocator, Logger       |
        | Enricher               |   |                              |
        +------------+-----------+   +----------+-------------------+
                     |                          |
                Infrastructure adapters (the only layer importing pyautogui,
                uiautomation, pywinauto, pynput, torch, ctypes)
```

### Component Boundaries

| Component | Responsibility | Depends on | Talks to |
|-----------|----------------|------------|----------|
| `domain.timeline` | Dataclasses for timeline items, normalized `Action`, `ElementQuery`, `ElementRef`, `Rect`. Pure, no I/O. | stdlib | everything |
| `timeline.loader` / `timeline.normalizer` | Parse JSON, normalize items to `Action` (the current `normalize_timeline` plus the `_scale_point`/resolution helpers). Pure functions plus a file loader. | domain | replay engine |
| `actions.base` | `Action` handler Protocol, `ActionRegistry`, `ActionContext` | domain | handlers |
| `actions.primitives` | Small `InputBackend` Protocol (mouse/keyboard/wait). `PyAutoGuiBackend` adapter. | domain | infra |
| `actions.handlers.*` | One handler per type (click, move, scroll, type, drag, hover, combo_key and so on), each registered. Replaces the Level0/Level1 god-classes. | `InputBackend` | registry |
| `replay.engine` | Loop, dry-run, delay, `continue_on_error`, `ActionResult/ActionError`, minimize-first. Knows nothing about pyautogui. | registry, `Clock`, `Logger` | CLI |
| `replay.strategies` | Per-mode resolution strategies: `CoordinateStrategy` (Naive), `AxFirstStrategy` (Timeline AX, falling back to coordinates), `DragLiveStrategy`. | `ElementLocator`, handlers | engine |
| `ax.locator` | `ElementLocator` Protocol. `UiAutomationLocator` adapter wraps `uiautomation`. Tree walk, query matching, `live_point`. | domain | strategies, REPL |
| `ax.matching` | The existing `Accesibilitytree` ClickElement/TypingElement/Drag/Scrolling finders, kept as pure matchers over recorded tree data. | domain | enrichment stage |
| `pipeline.stages` | Each stage as a class implementing `Stage.run(ctx) -> StageResult`. Wraps the existing module logic. | domain, ports | `PipelineRunner` |
| `pipeline.runner` | Takes an ordered list of `Stage` objects (injected), applies selection and skip/only, and reports. | `Stage` Protocol | CLI (`main.py`) |
| `cli.*` | argparse and REPL only. | application | user |

### Data Flow

Record time and enrich time (offline, file-based):

```
raw episode (screenshots, mouse/keyboard events, AX json)
  -> OmniParser stage     (annotates screenshots)
  -> mapper stage         (clicks/scrolls -> element under cursor)
  -> keyboard stage       (typed text -> element)
  -> crop stage           (unnamed clicks -> crops for the VLM)
  -> axtree stage         (click/drag/typing -> element in AX tree)
  -> enriched timeline.json
```

Stages communicate through the episode directory (the timeline file). This is a deliberate, existing contract. Keep it, and make it explicit by passing a `StageContext(episode_dir, dry_run, quiet, force, device)` object rather than argv strings.

Replay time (online):

```
timeline.json -> loader -> normalizer -> list[Action]
  -> ReplayEngine --for each action--> Strategy.resolve(action)   (uses ElementLocator; may fall back)
                                    -> ActionRegistry.get(type).execute(ctx)
                                    -> InputBackend (pyautogui)
  -> list[ActionResult]
```

Direction is one-way: timeline -> engine -> handlers -> backend. Handlers never import the engine. The locator never imports handlers.

## Patterns to Follow

### Pattern 1: Executor as Strategy plus one engine (not an Executor class hierarchy)
**What:** The four executors differ mainly in how a target point is resolved (recorded coordinates, AX lookup with fallback, live drag endpoints). They do not differ in the loop. Extract one `ReplayEngine` and make `ResolutionStrategy` the varying part. Do not build an ABC chain `NaiveExecutor -> AxExecutor -> ...`, because that invites LSP violations (for example a drag-only executor that cannot run clicks).
**When:** Always for replay. Keep `NaiveExecutor`, `TimelineAxExecutor` and `DragDropExecutor` as thin backward-compatible facades that construct the engine with the right strategy. This preserves public APIs.
```python
class Executor(Protocol):            # the common interface the facades satisfy (ISP: one method)
    def run(self, actions: Sequence[Mapping[str, Any]]) -> list[ActionResult]: ...

class ResolutionStrategy(Protocol):
    def resolve(self, action: Action) -> ResolvedTarget: ...

class ReplayEngine:
    def __init__(self, registry: ActionRegistry, strategy: ResolutionStrategy,
                 policy: ErrorPolicy, clock: Clock, log: Callable[[str], None]): ...
```
Use `Protocol` for `Executor` so the facades need not inherit from it. Use ABC only if a shared template method is truly needed.

### Pattern 2: Registry of action handlers for OCP
**What:** Replace `ActionLevel0/1` method bundles and `_do_*` dispatch with one small class per action type, registered by name. Adding an action means adding a file and a register call, with no edit to the engine or the dispatcher.
```python
class ActionHandler(Protocol):
    type_name: ClassVar[str]
    def execute(self, params: Mapping[str, Any], ctx: ActionContext) -> None: ...

class ActionRegistry:
    def register(self, h: ActionHandler) -> None: ...
    def get(self, type_name: str) -> ActionHandler: ...   # raises UnknownActionError
```
Level1 macros become handlers composed from lower-level handlers or from `InputBackend` calls (composite, not inheritance). Keep `ActionLevel0` and `ActionLevel1` as deprecated shims delegating to the handlers until all callers have moved over. Move the validators (`_number`, `_button`, `_key`) into a `validation` module so handlers share them (SRP).

### Pattern 3: Narrow ports, constructor injection (DIP and ISP)
**What:** `InputBackend` (move, click, down/up, key, scroll) and `ElementLocator` (find, rect, point) are separate, small Protocols. Test code passes a `FakeBackend` that records calls. A composition root (`build_default_engine()`) is the only place that imports pyautogui and uiautomation. This also fixes `TimelineAxExecutor` instantiating them directly, and it makes the dry-run behaviour a `NullBackend` rather than `if not self.dry_run` checks scattered through the code.

### Pattern 4: Stage Protocol plus injected ordered list
**What:** Wrap each pipeline module in a `Stage` class (`name`, `title`, `run(ctx) -> int`). `main.py` keeps the `STAGES` order, but it becomes a list of `Stage` instances built by a factory. The `accepts` flag dict is replaced by `StageContext`, and each stage reads only the fields it needs. Stages stay lazily imported inside their `run` (torch is heavy). Existing `module.main(argv)` stays callable, so the old CLIs keep working. Start with an `ArgvModuleStage` adapter that wraps the legacy `main(argv)` and move to native stages one at a time.

### Pattern 5: Separate parse, match and act inside the AX code
**What:** Split `accessibility_tree_executor.py` into data classes (`ElementQuery`, `ElementInfo` and so on, which go to the domain), a locator (find), a performer (click, type, scroll, via `InputBackend`) and the REPL (CLI). Rename its `Executor` class to avoid the collision with `executor.Executor`, and leave an alias for compatibility.

## Anti-Patterns to Avoid

### Anti-Pattern 1: Deep executor inheritance
**Why bad:** The variants differ in resolution, not in the loop. Inheritance gives LSP traps and fragile base classes.
**Instead:** Composition (engine plus strategy), with the old class names kept as facades.

### Anti-Pattern 2: Java-style interface-per-class
**Why bad:** Python has no need for an `IFoo` for every class. Interfaces with a single implementation and no fake add noise.
**Instead:** Add a Protocol only at a real seam: hardware backend, UI locator, pipeline stage, resolution strategy.

### Anti-Pattern 3: Big-bang rewrite or package move first
**Why bad:** It breaks the entry points that `main.py` resolves by module name (`mousemapper`, `node`, `click_content`) and breaks the import paths used by the tests.
**Instead:** Create the new packages, move logic behind facades, and keep the old module names as re-export shims until the final phase.

### Anti-Pattern 4: Raw `dict` actions everywhere
**Why bad:** Every executor re-validates by hand and the schema is implicit.
**Instead:** Normalize once to a frozen `Action` dataclass at the boundary (the loader). Handlers receive typed params.

### Anti-Pattern 5: Duplicate modules and the `DataMapper.py/` directory (a directory with a `.py` name)
**Instead:** Pick one canonical copy of each before any move.

## Suggested Build Order (dependency-driven)

Each step has tests that pin the existing behaviour before the code changes. Steps are ordered so that every step depends only on earlier ones.

0. **Safety net and hygiene.** Resolve duplicate and deleted files, and rename the `DataMapper.py` directory. Add characterization tests for `normalize_timeline` (pure, easy), `NaiveExecutor` in dry-run mode, and the `ActionLevel0/1` validators using a fake backend. The existing finder tests stay. Requires no design decisions, and everything else depends on it.
1. **Domain types and timeline normalizer extraction.** Move the pure functions out of `executor.py` into `timeline/`, and move `ElementQuery`, `ElementInfo` and `Rect` into domain. `executor.py` re-imports them. This breaks the `timeline_ax_executor -> executor` and `-> accessibility_tree_executor` coupling, which is the root of the import tangle.
2. **Backend and locator ports.** Define the `InputBackend` and `ElementLocator` Protocols, the pyautogui adapter, a `NullBackend` for dry runs, and a `FakeBackend` for tests. Make `ActionLevel0` use the backend through injection (it already has a `_backend()` seam).
3. **Action registry and handlers (OCP).** Split Level0/Level1 into handlers, make the registry the dispatcher, and keep the shim classes. Verify against the characterization tests.
4. **ReplayEngine and `Executor` Protocol.** Extract the loop and error policy out of `NaiveExecutor`. `NaiveExecutor` becomes a facade with `CoordinateStrategy`.
5. **AX layer split.** Divide `accessibility_tree_executor.py` into locator, performer and REPL. Introduce `UiAutomationLocator`. Then re-express `TimelineAxExecutor` as the engine plus `AxFirstStrategy`, and `DragDropExecutor` as the engine plus `DragLiveStrategy`. This is the riskiest step because it needs live-desktop behaviour. Use fake locators with scripted trees for tests, and do a manual smoke test on a real desktop.
6. **Pipeline stages.** Add the `Stage` Protocol, the `ArgvModuleStage` adapter and `PipelineRunner`, rewire `main.py`, then migrate the stages one at a time (mapper, keyboard, crop, axtree, OmniParser). It is independent of steps 2 to 5 and can run in parallel with them if the team allows. Within it, `Accesibilitytree` matcher cleanup is low risk because the package is already well-formed and tested.
7. **Package layout and shim removal.** Move to the final package structure, retain the top-level re-export shims for one release, and fix the CLI entry points in `pyproject.toml`.

**Parallelism:** Steps 1 to 5 form the replay track (strictly sequential). Step 6 is the pipeline track and shares only step 0 and the domain types from step 1. Step 7 comes last.

## Scalability Considerations

This is a single-user desktop tool, so scale is not the concern. The extensibility axes that matter are:

| Concern | Today | After refactor |
|---------|-------|----------------|
| New action type | Edit `_handlers`, the Level1 class and several executors | Add one handler file and register it |
| New resolution mode | New executor class with a copy of the loop | New `ResolutionStrategy` |
| New enrichment stage | Edit the `STAGES` dicts plus add a CLI `main(argv)` | Add a `Stage` class and one list entry |
| Testing without a desktop | Not possible (pyautogui and uiautomation are called directly) | `FakeBackend` and `FakeLocator` |

## Sources

- Direct code reading: `executor.py`, `action_classes.py`, `main.py`, `timeline_ax_executor.py`, `drag_drop_executor.py`, `accessibility_tree_executor.py`, `.planning/PROJECT.md` (HIGH confidence for the current-state facts).
- Patterns (Protocol for structural typing, composition over inheritance, registry for OCP, composition root): general Python design practice (PEP 544 `typing.Protocol`). From training knowledge and not re-verified against current docs (MEDIUM confidence). These are stable, long-established idioms.

## Gaps

- Not read in detail: `mousemapper.py`, `node.py`, `click_content.py`, `Accesibilitytree/*`, and `Omniparser_Runner`. The stage-by-stage migration complexity in step 6 is an estimate. Phase-specific research should read them.
- It is unknown which of the duplicate files are canonical, and which deleted files (`find_element.py`, `automation_script.py` and so on) are intended deletions.
- Live-desktop behaviour (DPI awareness, hit testing in `drag_drop_executor.py`) cannot be unit-tested. A manual smoke-test checklist is needed for step 5.
