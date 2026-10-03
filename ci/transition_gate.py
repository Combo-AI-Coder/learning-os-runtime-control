"""Validate one publication against the previously accepted Core semantics.

No deployment mutation, network access, learner data or second transition model.
A PR compares base -> prospective merge; a push compares before -> after.
Intermediate commits in one atomic publication are not observed freeze stages.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

TRUSTED_CORE_ID = 1343815302
TRUSTED_CONTROL_ID = 1343816194
EXACT_SHA = re.compile(r"[0-9a-f]{40}")
MAX_DOCUMENT_BYTES = 1024 * 1024


def exact_sha(value: object) -> str:
    if not isinstance(value, str) or not EXACT_SHA.fullmatch(value) or value == "0" * 40:
        raise ValueError("an existing exact commit SHA is required")
    return value


def baseline_for_event(event_name: str, event: dict) -> str:
    repository = event.get("repository")
    if (not isinstance(repository, dict) or type(repository.get("id")) is not int
            or repository["id"] != TRUSTED_CONTROL_ID):
        raise ValueError("event repository identity does not match Runtime-Control")
    if event_name == "pull_request":
        pr = event.get("pull_request")
        base = pr.get("base") if isinstance(pr, dict) else None
        if not isinstance(base, dict) or base.get("ref") != "main":
            raise ValueError("pull request must target canonical main")
        return exact_sha(base.get("sha"))
    if event_name == "push":
        if event.get("ref") != "refs/heads/main":
            raise ValueError("push must target canonical main")
        if any(event.get(flag) is not False for flag in ("created", "deleted", "forced")):
            raise ValueError("creation, deletion, force push or missing event flags require separate review")
        return exact_sha(event.get("before"))
    raise ValueError("unsupported event: no trusted comparison baseline")


def bounded_text(path: Path) -> str:
    with path.open("rb") as source:
        raw = source.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("document exceeds the CI admission limit")
    return raw.decode("utf-8")


def core_pin(text: str) -> str:
    # Only accepted baseline input reaches this bootstrap parser. Both full
    # documents are subsequently checked by that Core's bounded loader/schema.
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError("baseline exceeds the CI admission limit")
    document = yaml.safe_load(text)
    core = document.get("core") if isinstance(document, dict) else None
    if (not isinstance(core, dict) or type(core.get("repository_id")) is not int
            or core["repository_id"] != TRUSTED_CORE_ID):
        raise ValueError("baseline Core numeric identity is not trusted")
    return exact_sha(core.get("commit"))


def git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repository), *arguments],
        text=True, encoding="utf-8", timeout=30,
    )


def git_contract(repository: Path, commit: str) -> str:
    """Read the committed document, not a possibly modified working-tree file."""
    spec = f"{exact_sha(commit)}:deployment.yaml"
    size = int(git(repository, "cat-file", "-s", spec).strip())
    if size < 0 or size > MAX_DOCUMENT_BYTES:
        raise ValueError("committed contract exceeds the CI admission limit")
    return git(repository, "show", spec)


def prepare(repository: Path, event_name: str, event: dict, current_sha: str,
            previous_out: Path) -> str:
    baseline = baseline_for_event(event_name, event)
    current_sha = exact_sha(current_sha)
    if event_name == "push" and event.get("after") != current_sha:
        raise ValueError("push after SHA does not match the checked candidate")
    if git(repository, "rev-parse", "HEAD").strip() != current_sha:
        raise ValueError("checkout does not match the event candidate")
    git(repository, "merge-base", "--is-ancestor", baseline, current_sha)
    previous = git_contract(repository, baseline)
    pin = core_pin(previous)
    previous_out.write_text(previous, encoding="utf-8", newline="\n")
    return pin


def validate_pair(previous_text: str, current_text: str) -> None:
    # These imports must resolve from the exact previously deployed Core,
    # never from the candidate Core being admitted by this transition.
    from scripts.runtime_adapter import _load_contract, validate_deployment_transition
    from scripts.validate_learning_os import validate_deployment_contract_document
    previous, current = _load_contract(previous_text), _load_contract(current_text)
    for label, text, document in (("previous", previous_text, previous),
                                  ("candidate", current_text, current)):
        core_pin(text)
        errors = [finding for finding in validate_deployment_contract_document(
            document, path=f"{label}/deployment.yaml", raw_text=text
        ) if finding.severity == "error"]
        if errors:
            raise ValueError(label + " contract failed: " + ", ".join(sorted({f.code for f in errors})))
    validate_deployment_transition(previous, current)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--repository", type=Path, default=Path("."))
    prep.add_argument("--event-name", required=True)
    prep.add_argument("--event-path", type=Path, required=True)
    prep.add_argument("--current-sha", required=True)
    prep.add_argument("--previous-out", type=Path, required=True)
    check = commands.add_parser("check")
    check.add_argument("--previous", type=Path, required=True)
    check.add_argument("--repository", type=Path, default=Path("."))
    check.add_argument("--current-sha", required=True)
    check.add_argument("--trusted-core-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            event = json.loads(bounded_text(args.event_path))
            if not isinstance(event, dict):
                raise ValueError("event must be an object")
            pin = prepare(args.repository, args.event_name, event, args.current_sha, args.previous_out)
            print("core_commit=" + pin)
        else:
            previous = bounded_text(args.previous)
            if git(args.trusted_core_root, "rev-parse", "HEAD").strip() != core_pin(previous):
                raise ValueError("trusted validator checkout is not the baseline Core pin")
            sys.path.insert(0, str(args.trusted_core_root.resolve()))
            current = exact_sha(args.current_sha)
            if git(args.repository, "rev-parse", "HEAD").strip() != current:
                raise ValueError("candidate checkout changed after event preparation")
            validate_pair(previous, git_contract(args.repository, current))
            print("Runtime-Control publication transition PASS (not deployment acceptance)")
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, yaml.YAMLError) as exc:
        print(f"transition gate rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
