#!/usr/bin/env python3
"""Find and optionally remove old Claude activation session files."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from activation import resolve_activation_session_path


DEFAULT_PROMPT = "."
DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


@dataclass(frozen=True)
class Candidate:
    """A validated activation session candidate and its observed size."""

    path: Path
    size: int


class CleanupPathError(ValueError):
    """Raised when the projects root cannot be used for a cleanup scan."""


def resolve_projects_root(value: Path | str) -> Path:
    """Resolve an existing projects directory, failing before any scan."""
    try:
        root = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise CleanupPathError(f"invalid projects root: {value}") from error
    if not root.is_dir():
        raise CleanupPathError(f"projects root is not a directory: {root}")
    return root


def _session_id_from_path(path: Path) -> str | None:
    if path.suffix != ".jsonl":
        return None
    session_id = path.stem
    try:
        parsed = uuid.UUID(session_id)
    except (AttributeError, TypeError, ValueError):
        return None
    if str(parsed) != session_id.lower():
        return None
    return session_id


def _validated_candidate_path(projects_root: Path, path: Path) -> Path | None:
    """Return a file path only when the filename and resolved path are safe."""
    session_id = _session_id_from_path(path)
    if session_id is None:
        return None

    try:
        allowed_root = projects_root.resolve()
        if path.parent != allowed_root / "-":
            return None
        candidate = resolve_activation_session_path(allowed_root, session_id)
        if candidate is None:
            return None
        candidate.resolve().relative_to(allowed_root)
        if candidate.is_symlink() or not candidate.is_file():
            return None
    except (OSError, RuntimeError, TypeError, ValueError):
        return None

    return candidate


def _prompt_matches(record: dict, prompt: str) -> bool:
    record_type = record.get("type")
    if record_type == "queue-operation":
        return record.get("content") == prompt
    if record_type != "user":
        return False

    if record.get("content") == prompt:
        return True
    message = record.get("message")
    return isinstance(message, dict) and message.get("content") == prompt


def _entrypoint_matches(record: dict) -> bool:
    entrypoint = record.get("entrypoint")
    return isinstance(entrypoint, str) and entrypoint.startswith("sdk-cli")


def is_activation_session(path: Path, prompt: str = DEFAULT_PROMPT) -> bool:
    """Check activation markers without loading the whole JSONL into memory."""
    prompt_found = False
    entrypoint_found = False

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return False
                if not isinstance(record, dict):
                    continue

                if "cwd" in record and record["cwd"] is not None:
                    if record["cwd"] != "/":
                        return False
                prompt_found = prompt_found or _prompt_matches(record, prompt)
                entrypoint_found = entrypoint_found or _entrypoint_matches(record)

    except (OSError, UnicodeError):
        return False

    return prompt_found and entrypoint_found


def _iter_session_files(projects_root: Path) -> Iterator[Path]:
    """Yield only direct children of the safe ``-`` project directory."""
    session_dir = projects_root / "-"
    try:
        resolved_session_dir = session_dir.resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise CleanupPathError(f"cannot read projects root: {projects_root}") from error
    try:
        resolved_session_dir.relative_to(projects_root)
    except ValueError:
        return
    if session_dir.is_symlink():
        return
    if not session_dir.is_dir():
        return
    try:
        entries = sorted(session_dir.iterdir(), key=lambda item: item.name)
    except OSError as error:
        raise CleanupPathError(f"cannot read projects root: {projects_root}") from error

    for entry in entries:
        if entry.is_file() or entry.is_symlink():
            yield entry


def find_candidates(projects_root: Path | str, prompt: str = DEFAULT_PROMPT) -> list[Candidate]:
    """Return activation candidates under one resolved projects root."""
    root = resolve_projects_root(projects_root)
    candidates: list[Candidate] = []
    for path in _iter_session_files(root):
        safe_path = _validated_candidate_path(root, path)
        if safe_path is None or not is_activation_session(safe_path, prompt):
            continue
        try:
            size = safe_path.stat().st_size
        except OSError:
            continue
        candidates.append(Candidate(safe_path, size))
    return candidates


def _print_candidates(candidates: list[Candidate]) -> None:
    print("candidates:")
    for candidate in candidates:
        print(f"- {candidate.path}")
    print(f"candidate count: {len(candidates)}")
    print(f"total bytes: {sum(candidate.size for candidate in candidates)}")


@dataclass
class CleanupSummary:
    deleted: int = 0
    skipped: int = 0
    failed: int = 0


def apply_cleanup(
    projects_root: Path | str,
    candidates: list[Candidate],
    prompt: str = DEFAULT_PROMPT,
) -> CleanupSummary:
    """Delete only candidates that still pass every safety check."""
    root = resolve_projects_root(projects_root)
    summary = CleanupSummary()
    for candidate in candidates:
        safe_path = _validated_candidate_path(root, candidate.path)
        if safe_path is None or not is_activation_session(safe_path, prompt):
            summary.skipped += 1
            continue
        try:
            safe_path.unlink()
        except (OSError, RuntimeError) as error:
            summary.failed += 1
            print(f"warning: could not delete {safe_path}: {error}", file=sys.stderr)
        else:
            summary.deleted += 1
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find old Claude activation sessions; dry-run is the default."
    )
    parser.add_argument(
        "--projects-root",
        type=Path,
        default=DEFAULT_PROJECTS_ROOT,
        help="Claude projects directory (default: ~/.claude/projects)",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="activation prompt to match exactly (default: .)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="apply",
        action="store_false",
        help="list candidates without deleting files (default)",
    )
    mode.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="delete only validated candidates",
    )
    parser.set_defaults(apply=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        candidates = find_candidates(args.projects_root, args.prompt)
    except CleanupPathError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    _print_candidates(candidates)
    if not args.apply:
        print("dry-run: no files deleted")
        return 0

    try:
        summary = apply_cleanup(args.projects_root, candidates, args.prompt)
    except CleanupPathError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"deleted: {summary.deleted}")
    print(f"skipped: {summary.skipped}")
    print(f"failed: {summary.failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
