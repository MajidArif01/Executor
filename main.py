"""Run the whole episode pipeline end to end, from one episode path.

The four stages each already work on a single episode and each take
``--episode-dir``; this script is the sequencer that runs them in the only
order that makes sense, since every stage consumes what the previous one wrote:

  1. ``Omniparser_Runner``  parse every typing / click / scroll screenshot into
                            ``AnotatedData/AnotatedJson/{keyboard,mouse,scroll}/``
  2. ``mousemapper.py``     match each click and scroll node's (x, y) to the
                            element under it, appended to ``timeline.json``
  3. ``node.py``            match each Typing node's typed string to the element
                            showing it, appended to the same ``timeline.json``
  4. ``click_content.py``   crop the clicks nobody could name into ``base64.json``

Stages 2 and 3 both mutate ``timeline.json`` in place, so they run one after the
other rather than together. A stage that fails stops the run: stage 2 has
nothing to match without stage 1's output, and it would write ``no_json``
placeholders over a half-parsed episode.

Usage::

    python main.py "D:/My Desktop/Orca_Observation/episode/gourmet-tree"
    python main.py gourmet-tree                      # resolved under EPISODES_ROOT
    python main.py gourmet-tree --device cuda:0
    python main.py gourmet-tree --from mapper        # skip the parse, re-run the rest
    python main.py gourmet-tree --only crop
    python main.py gourmet-tree --dry-run            # stages 2-4 match and print only
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Omniparser_Runner.config import EPISODES_ROOT, TIMELINE_RELPATH  # noqa: E402

# Stage order is the pipeline. Each entry names the module whose ``main(argv)``
# runs it, and which of this script's pass-through options it understands -- the
# stages don't share a flag vocabulary (only the runner knows ``--device``, only
# the keyboard matcher knows ``--force``), so each is told what it accepts.
STAGES = [
    {
        "name": "parse",
        "module": "Omniparser_Runner.cli",
        "title": "OmniParser: annotate node screenshots",
        "accepts": {"types", "device", "quiet"},
    },
    {
        "name": "mapper",
        "module": "mousemapper",
        "title": "Mouse mapper: click / scroll -> element under the cursor",
        "accepts": {"dry_run", "quiet"},
    },
    {
        "name": "keyboard",
        "module": "node",
        "title": "Keyboard mapper: typed text -> element showing it",
        "accepts": {"dry_run", "quiet", "force"},
    },
    {
        "name": "crop",
        "module": "click_content",
        "title": "Click content: crop the clicks with no name",
        "accepts": {"dry_run"},
    },
]

STAGE_NAMES = [stage["name"] for stage in STAGES]


def resolve_episode_dir(raw: str, episodes_root: Path) -> Path:
    """An episode path, or a bare episode name resolved under ``episodes_root``."""
    candidate = Path(raw).expanduser()
    if candidate.is_dir():
        return candidate.resolve()

    under_root = (episodes_root / raw).expanduser()
    if under_root.is_dir():
        return under_root.resolve()

    raise FileNotFoundError(f"No episode at {candidate} or {under_root}")


def stage_argv(stage: dict, episode_dir: Path, args: argparse.Namespace) -> list[str]:
    """The command line for one stage: the episode, plus the options it accepts."""
    argv = ["--episode-dir", str(episode_dir)]
    accepts = stage["accepts"]

    if "types" in accepts and args.types:
        argv += ["--types", args.types]
    if "device" in accepts and args.device:
        argv += ["--device", args.device]
    if "dry_run" in accepts and args.dry_run:
        argv.append("--dry-run")
    if "force" in accepts and args.force:
        argv.append("--force")
    if "quiet" in accepts and args.quiet:
        argv.append("--quiet")
    return argv


def run_stage(stage: dict, episode_dir: Path, args: argparse.Namespace) -> int:
    """Run one stage in this process and return its exit code.

    The stages are imported rather than shelled out to, so one interpreter (and
    one loaded model) serves the whole run. Each ``main`` reports failure its own
    way -- a return code, or ``sys.exit`` from the ones written as scripts -- so
    both are funnelled into a single code here.
    """
    module = __import__(stage["module"], fromlist=["main"])
    argv = stage_argv(stage, episode_dir, args)

    try:
        code = module.main(argv)
    except SystemExit as exc:
        code = exc.code
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error [{stage['name']}]: {exc}", file=sys.stderr)
        return 1

    if code is None:
        return 0
    return code if isinstance(code, int) else 1


def split_names(raw: str) -> set[str]:
    """Parse a comma-separated stage list, rejecting names that aren't stages."""
    names = {name.strip() for name in raw.split(",") if name.strip()}
    unknown = names - set(STAGE_NAMES)
    if unknown:
        raise ValueError(f"Unknown stage(s): {', '.join(sorted(unknown))}")
    return names


def select_stages(args: argparse.Namespace) -> list[dict]:
    """The stages to run, after ``--only`` / ``--from`` / ``--skip``."""
    if args.only:
        wanted = split_names(args.only)
        selected = [stage for stage in STAGES if stage["name"] in wanted]
    else:
        start = STAGE_NAMES.index(args.start) if args.start else 0
        selected = STAGES[start:]

    if args.skip:
        skipped = split_names(args.skip)
        selected = [stage for stage in selected if stage["name"] not in skipped]

    if not selected:
        raise ValueError("Stage selection left nothing to run")
    return selected


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Run the full episode pipeline: OmniParser -> mouse mapper "
        "-> keyboard mapper -> click content.",
    )
    parser.add_argument(
        "episode",
        help="Path to an episode folder, or an episode name under --episodes-root",
    )
    parser.add_argument(
        "--episodes-root", default=str(EPISODES_ROOT),
        help="Root a bare episode name is resolved against",
    )
    parser.add_argument(
        "--only", default=None, metavar="STAGES",
        help=f"Run only these comma-separated stages ({', '.join(STAGE_NAMES)})",
    )
    parser.add_argument(
        "--from", dest="start", default=None, choices=STAGE_NAMES,
        help="Start at this stage and run the rest",
    )
    parser.add_argument(
        "--skip", default=None, metavar="STAGES",
        help="Comma-separated stages to leave out",
    )
    parser.add_argument(
        "--types", default=None,
        help="Node kinds for the OmniParser stage: typing, mouse, scroll "
             "(default: all three)",
    )
    parser.add_argument("--device", default=None, help="cuda, cuda:0 or cpu")
    parser.add_argument(
        "--force", action="store_true",
        help="Re-match typing nodes that already carry a block",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Match and print in the stages that support it; write nothing",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        episode_dir = resolve_episode_dir(args.episode, Path(args.episodes_root))
        stages = select_stages(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    timeline_path = episode_dir / TIMELINE_RELPATH
    if not timeline_path.is_file():
        print(f"Error: no timeline at {timeline_path}", file=sys.stderr)
        return 1

    print(f"Episode:  {episode_dir}")
    print(f"Timeline: {timeline_path}")
    print(f"Stages:   {' -> '.join(stage['name'] for stage in stages)}")

    started = time.monotonic()
    for position, stage in enumerate(stages, start=1):
        print(f"\n=== [{position}/{len(stages)}] {stage['name']}: {stage['title']} ===")
        stage_started = time.monotonic()
        code = run_stage(stage, episode_dir, args)
        elapsed = time.monotonic() - stage_started

        if code:
            print(
                f"\nStage {stage['name']!r} failed (exit {code}) after "
                f"{elapsed:.1f}s -- stopping, since the stages after it read "
                f"what this one writes.",
                file=sys.stderr,
            )
            return code
        print(f"--- {stage['name']} done in {elapsed:.1f}s ---")

    print(f"\nPipeline finished in {time.monotonic() - started:.1f}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
