"""Shared cross-harness canonical-writer boundary.

This module is intentionally a thin CLI over ``personal_ai_sync.CanonicalMutationLock``.
It does not create a second lock, infer ownership from process names, or repair foreign
work.  Git hooks and the DSH guard call this boundary before allowing a canonical write.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import personal_ai_sync as pas  # noqa: E402


WRITER_REQUIRED_ENV = (
    "PERSONAL_AI_MUTATION_LEASE_ID",
    "PERSONAL_AI_MUTATION_RUN_ID",
)
GOVERNED_PUSH_ENV = "PERSONAL_AI_GOVERNED_PUSH"
PUSH_AUTH_ENV = "PERSONAL_AI_PUSH_AUTHORIZATION"
PATH_KEYS = {"path", "file", "filename", "file_path", "filePath", "target", "targets", "files"}
SHELL_KEYS = {"command", "cmd", "script", "line", "text"}
GIT_BROAD_RE = re.compile(r"(?:^|\s)(?:\.|\.\.|\*|-A|--all|--update|scripts/?)(?:\s|$)")


def _repo_from_git(cwd: Path) -> Path | None:
    probe = cwd if cwd.is_dir() else cwd.parent
    rc, out = pas.git(probe, "rev-parse", "--show-toplevel")
    if rc != 0 or not out.strip():
        return None
    return Path(out.splitlines()[0].strip()).resolve(strict=False)


def resolve_repo(raw: str | None) -> Path:
    if raw:
        repo = Path(raw).resolve(strict=False)
    else:
        repo = _repo_from_git(Path.cwd())
        if repo is None:
            raise RuntimeError("REPOSITORY_NOT_FOUND")
    if not (repo / ".git").exists():
        raise RuntimeError(f"NOT_A_GIT_REPOSITORY: {repo}")
    return repo


def _lock_root(repo: Path, override: str | None = None) -> Path:
    return Path(override).resolve(strict=False) if override else pas._mutation_lock_root_for_repo(repo)


def _canonical_or_error(repo: Path) -> None:
    if not pas._is_canonical_mutation_repo(repo):
        raise pas.MutationOwnershipError(
            "NON_CANONICAL",
            f"DEFER: canonical writer is denied for non-canonical repository {repo}",
        )


def _relative_paths(repo: Path, raw_paths: list[str]) -> list[str]:
    values: list[str] = []
    for raw in raw_paths:
        candidate = Path(str(raw))
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve(strict=False).relative_to(repo.resolve(strict=False))
            except ValueError as exc:
                raise ValueError(f"owned path is outside repository: {raw}") from exc
        values.append(str(candidate).replace("\\", "/"))
    return pas._normalise_scope_paths(values)


def _requested_scope(args: argparse.Namespace, repo: Path) -> list[str]:
    paths = list(getattr(args, "path", []) or [])
    scopes = list(getattr(args, "scope", []) or [])
    values = _relative_paths(repo, paths) if paths else []
    values.extend(scopes)
    if not values:
        return []
    return pas._validate_scope(values, scope_contract=getattr(args, "scope_contract", None))


def _lease_token_matches(lease: dict, args: argparse.Namespace) -> bool:
    lease_id = getattr(args, "lease_id", None) or os.environ.get(WRITER_REQUIRED_ENV[0])
    run_id = getattr(args, "run_id", None) or os.environ.get(WRITER_REQUIRED_ENV[1])
    if not lease_id or not run_id:
        return False
    return lease.get("mutation_lease_id") == lease_id and lease.get("run_id") == run_id


def _harness_matches(lease: dict, args: argparse.Namespace) -> bool:
    requested = getattr(args, "harness", None) or os.environ.get("PERSONAL_AI_HARNESS")
    recorded = lease.get("harness")
    if not requested or requested == pas.PROVENANCE_UNKNOWN or not recorded:
        return True
    return requested == recorded


def _load_active_lock(
    repo: Path,
    args: argparse.Namespace,
    *,
    scope: list[str] | None = None,
    require_token: bool = True,
) -> pas.CanonicalMutationLock:
    root = _lock_root(repo, getattr(args, "lock_root", None))
    lease = pas.read_active_mutation_lease(repo, lock_root=root)
    if not lease or not lease.get("active"):
        raise pas.MutationOwnershipError(
            "NO_ACTIVE_CANONICAL_WRITER",
            "NO_ACTIVE_CANONICAL_WRITER: no live shared lease exists",
            lease or {},
        )
    if require_token and not _lease_token_matches(lease, args):
        raise pas.MutationOwnershipError(
            "DEFER_FOREIGN_CANONICAL_WRITER",
            "DEFER_FOREIGN_CANONICAL_WRITER: active lease is not bound to this process",
            lease,
        )
    if not _harness_matches(lease, args):
        raise pas.MutationOwnershipError(
            "DEFER_FOREIGN_CANONICAL_WRITER",
            "DEFER_FOREIGN_CANONICAL_WRITER: active lease belongs to another harness",
            lease,
        )
    if scope:
        owned = pas._validate_scope(
            [str(item) for item in lease.get("scope", [])],
            scope_contract=lease.get("scope_contract"),
        )
        foreign = sorted(path for path in scope if not pas._path_in_scope(path, owned))
        if foreign:
            raise pas.MutationOwnershipError(
                "ABORT_FOREIGN_STAGED_PATH",
                f"ABORT_FOREIGN_STAGED_PATH: lease does not own {foreign}",
                {**lease, "foreign_paths": foreign},
            )
    return pas.CanonicalMutationLock.from_metadata(
        lease,
        lock_root=root,
        receipt_root=root / "receipts",
        canonical_root=repo,
    )


def _new_lock(repo: Path, args: argparse.Namespace, scope: list[str], operation: str) -> pas.CanonicalMutationLock:
    harness = getattr(args, "harness", None) or os.environ.get("PERSONAL_AI_HARNESS") or pas.PROVENANCE_UNKNOWN
    actor = os.environ.get("PERSONAL_AI_ACTOR", harness)
    task_id = getattr(args, "task_id", None) or os.environ.get(
        "PERSONAL_AI_TASK_ID", "cross-harness-canonical-writer"
    )
    session_id = getattr(args, "session_id", None) or os.environ.get(
        "PERSONAL_AI_SESSION_ID", os.environ.get("PERSONAL_AI_THREAD_ID", pas.PROVENANCE_UNKNOWN)
    )
    return pas.CanonicalMutationLock(
        repo,
        actor=actor,
        actor_type=os.environ.get("PERSONAL_AI_ACTOR_TYPE", "automated"),
        harness=harness,
        trigger=os.environ.get("PERSONAL_AI_TRIGGER", "canonical_writer"),
        task_id=task_id,
        thread_id=os.environ.get("PERSONAL_AI_THREAD_ID", pas.PROVENANCE_UNKNOWN),
        session_id=session_id,
        cwd=str(Path.cwd().resolve(strict=False)),
        entrypoint=os.environ.get("PERSONAL_AI_ENTRYPOINT", str(Path(__file__).resolve(strict=False))),
        operation=operation,
        scope=scope,
        scope_contract=getattr(args, "scope_contract", None),
        run_id=getattr(args, "run_id", None) or os.environ.get("PERSONAL_AI_MUTATION_RUN_ID"),
        mutation_lease_id=getattr(args, "lease_id", None),
        lock_root=_lock_root(repo, getattr(args, "lock_root", None)),
        canonical_root=repo,
    )


def _print_error(exc: Exception) -> int:
    if isinstance(exc, pas.MutationOwnershipError):
        print(f"{exc.code}: {exc}")
        return 2
    print(f"CANONICAL_WRITER_ERROR: {exc}")
    return 2


def cmd_preflight(args: argparse.Namespace) -> int:
    try:
        repo = resolve_repo(args.repo)
        _canonical_or_error(repo)
        scope = _requested_scope(args, repo)
        if not scope and not getattr(args, "git_operation", None):
            raise pas.MutationOwnershipError(
                "UNKNOWN_MUTATION_PATH",
                "UNKNOWN_MUTATION_PATH: exact owned path is required for content mutation",
            )
        if args.git_operation == "push":
            if os.environ.get(GOVERNED_PUSH_ENV) != "1" or not os.environ.get(PUSH_AUTH_ENV):
                raise pas.MutationOwnershipError(
                    "PUSH_AUTHORIZATION_REQUIRED",
                    "PUSH_AUTHORIZATION_REQUIRED: raw push is not a governed mutation",
                )
        lock = _load_active_lock(repo, args, scope=scope)
        print(json.dumps({
            "status": "ALLOW",
            "code": "CANONICAL_MUTATION_ALLOWED",
            "repo": str(repo),
            "harness": lock.harness,
            "lease_id": lock.mutation_lease_id,
            "owned_paths": lock.scope,
            "requested_paths": scope,
        }, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 - boundary converts every failure to denial
        return _print_error(exc)


def _child_command(args: argparse.Namespace) -> list[str]:
    command = list(getattr(args, "command", []) or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ValueError("with-lease requires a child command after --")
    return command


def cmd_with_lease(args: argparse.Namespace) -> int:
    try:
        repo = resolve_repo(args.repo)
        _canonical_or_error(repo)
        scope = _requested_scope(args, repo)
        if not scope:
            raise ValueError("with-lease requires exact --path or explicit control --scope")
        lock = _new_lock(repo, args, scope, args.operation)
        command = _child_command(args)
        with lock:
            env = {
                **os.environ,
                "PERSONAL_AI_MUTATION_LEASE_ID": lock.mutation_lease_id,
                "PERSONAL_AI_MUTATION_RUN_ID": lock.run_id,
                "PERSONAL_AI_HARNESS": lock.harness,
                "PERSONAL_AI_TASK_ID": lock.task_id,
                "PERSONAL_AI_CANONICAL_WRITER": "1",
            }
            result = subprocess.run(command, cwd=str(repo), env=env, check=False)
            return result.returncode
    except Exception as exc:  # noqa: BLE001
        return _print_error(exc)


def cmd_hold(args: argparse.Namespace) -> int:
    try:
        repo = resolve_repo(args.repo)
        _canonical_or_error(repo)
        scope = _requested_scope(args, repo)
        if not scope:
            raise ValueError("hold requires exact --path or explicit control --scope")
        lock = _new_lock(repo, args, scope, args.operation)
        with lock:
            print(json.dumps(lock.metadata, ensure_ascii=False), flush=True)
            deadline = time.monotonic() + max(0, args.seconds)
            while time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
        return 0
    except Exception as exc:  # noqa: BLE001
        return _print_error(exc)


def _commit_with_lock(lock: pas.CanonicalMutationLock, args: argparse.Namespace, scope: list[str]) -> int:
    ok, message = pas._commit_owned_files_locked(
        lock,
        scope,
        allow_foreign_dirty=args.allow_foreign_dirty,
        message=args.message,
        validate=not args.no_validate,
    )
    print(message)
    return 0 if ok else 2


def cmd_commit(args: argparse.Namespace) -> int:
    try:
        repo = resolve_repo(args.repo)
        _canonical_or_error(repo)
        scope = _requested_scope(args, repo)
        if not scope or any(path in pas.CONTROL_SCOPES for path in scope):
            raise ValueError("commit requires exact content paths; control scopes are not stageable")
        try:
            lock = _load_active_lock(repo, args, scope=scope)
            return _commit_with_lock(lock, args, scope)
        except pas.MutationOwnershipError as exc:
            if exc.code not in {"NO_ACTIVE_CANONICAL_WRITER"}:
                raise
        lock = _new_lock(repo, args, scope, "owned-commit")
        with lock:
            return _commit_with_lock(lock, args, scope)
    except Exception as exc:  # noqa: BLE001
        return _print_error(exc)


def _remote_head(repo: Path, remote: str, branch: str) -> str:
    rc, out = pas.git(repo, "ls-remote", remote, f"refs/heads/{branch}", timeout=90)
    if rc != 0:
        raise RuntimeError(f"REMOTE_LOOKUP_FAILED: {out}")
    return out.split()[0].strip() if out.split() else "0" * 40


def _ahead_commits_have_receipts(repo: Path, branch: str, receipt_root: Path) -> tuple[bool, str]:
    commits = pas._local_ahead_commits(repo, branch)
    missing = [commit for commit in commits if not pas._owned_commit_receipt(repo, commit, receipt_root)]
    return (not missing, f"missing commit receipts: {missing}" if missing else "all ahead commits governed")


def _push_once(repo: Path, args: argparse.Namespace, lock: pas.CanonicalMutationLock) -> int:
    if pas.uncommitted_files(repo):
        raise RuntimeError("PUSH_AUTHORIZATION_DIRTY_WORKTREE")
    rc, branch = pas.git(repo, "symbolic-ref", "--short", "HEAD")
    branch = branch.strip()
    if rc != 0 or branch != args.branch:
        raise RuntimeError(f"PUSH_BRANCH_MISMATCH: expected={args.branch} actual={branch or pas.PROVENANCE_UNKNOWN}")
    rc, head = pas.git(repo, "rev-parse", "HEAD")
    if rc != 0:
        raise RuntimeError("PUSH_HEAD_UNAVAILABLE")
    head = head.strip()
    remote_before = _remote_head(repo, args.remote, args.branch)
    if remote_before == head:
        print("PUSH_NOOP: remote already equals HEAD")
        return 0
    ancestor_rc, _ = pas.git(repo, "merge-base", "--is-ancestor", remote_before, head)
    if ancestor_rc != 0:
        raise RuntimeError("PUSH_REMOTE_CHANGED_OR_DIVERGED")
    receipt_root = lock.lock_root / "receipts"
    governed, detail = _ahead_commits_have_receipts(repo, args.branch, receipt_root)
    if not governed:
        raise RuntimeError(f"PUSH_MISSING_COMMIT_RECEIPT: {detail}")
    if pas.privacy_scan(repo, f"{remote_before}..{head}"):
        raise RuntimeError("PUSH_PRIVACY_GATE")
    auth = pas.write_push_authorization(
        lock,
        remote=args.remote,
        branch=args.branch,
        expected_remote=remote_before,
        commit=head,
        base_head=remote_before,
        validation={"worktree": "CLEAN", "remote_gate": "PASS", "receipt_gate": "PASS"},
    )
    env = {
        "PERSONAL_AI_GOVERNED_PUSH": "1",
        "PERSONAL_AI_PUSH_AUTHORIZATION": str(auth),
        "PERSONAL_AI_MUTATION_LEASE_ID": lock.mutation_lease_id,
        "PERSONAL_AI_MUTATION_RUN_ID": lock.run_id,
    }
    rc, out = pas.run(["git", "-C", str(repo), "push", args.remote, args.branch], env=env, timeout=args.timeout)
    if rc != 0:
        raise RuntimeError(f"PUSH_FAILED: {out[-500:]}")
    remote_after = _remote_head(repo, args.remote, args.branch)
    if remote_after != head:
        raise RuntimeError("PUSH_REMOTE_RESULT_MISMATCH")
    receipt = pas.write_mutation_receipt(
        lock,
        base=remote_before,
        result="PUSHED",
        staged=[],
        changed=pas.changed_paths(repo, f"{remote_before}..{head}"),
        commit=head,
        base_head=remote_before,
        result_head=head,
        remote_before=remote_before,
        remote_after=remote_after,
        push_target=f"{args.remote}/{args.branch}",
        operation="explicit-push",
    )
    if not pas.validate_mutation_receipt(receipt, repo, commit=head, operation="explicit-push"):
        raise RuntimeError(f"PUSHED_WITHOUT_VALID_RECEIPT: {receipt}")
    print(f"PUSHED: {head} receipt={receipt}")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    try:
        repo = resolve_repo(args.repo)
        _canonical_or_error(repo)
        scope = ["git-history"]
        try:
            lock = _load_active_lock(repo, args, scope=scope)
            return _push_once(repo, args, lock)
        except pas.MutationOwnershipError as exc:
            if exc.code != "NO_ACTIVE_CANONICAL_WRITER":
                raise
        lock = _new_lock(repo, args, scope, "explicit-push")
        with lock:
            return _push_once(repo, args, lock)
    except Exception as exc:  # noqa: BLE001
        return _print_error(exc)


def _push_ref_once(repo: Path, args: argparse.Namespace, lock: pas.CanonicalMutationLock) -> int:
    ok, message = pas.push_governed_ref(
        lock,
        remote=args.remote,
        source_ref=args.source_ref,
        destination_ref=args.dest_ref,
        expected_remote=args.expected_remote or None,
        timeout=args.timeout,
    )
    print(message)
    return 0 if ok else 2


def cmd_push_ref(args: argparse.Namespace) -> int:
    try:
        repo = resolve_repo(args.repo)
        _canonical_or_error(repo)
        scope = ["git-history"]
        try:
            lock = _load_active_lock(repo, args, scope=scope)
            return _push_ref_once(repo, args, lock)
        except pas.MutationOwnershipError as exc:
            if exc.code != "NO_ACTIVE_CANONICAL_WRITER":
                raise
        lock = _new_lock(repo, args, scope, "push-ref")
        with lock:
            return _push_ref_once(repo, args, lock)
    except Exception as exc:  # noqa: BLE001
        return _print_error(exc)


def _intent_events(lock: pas.CanonicalMutationLock) -> list[tuple[Path, dict]]:
    root = lock.lock_root / "events"
    found: list[tuple[Path, dict]] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (value.get("event_type") == "COMMIT_INTENT"
                and value.get("run_id") == lock.run_id
                and value.get("repo") == str(lock.repo)):
            found.append((path, value))
    return found


def cmd_pre_commit(args: argparse.Namespace) -> int:
    try:
        repo = resolve_repo(args.repo)
        staged = pas._staged_paths(repo)
        if not staged:
            return 0
        lock = _load_active_lock(repo, args, scope=staged)
        base_rc, base = pas.git(repo, "rev-parse", "HEAD")
        if base_rc != 0:
            raise RuntimeError(f"COMMIT_BASE_UNAVAILABLE: {base}")
        event = pas.write_mutation_event(
            repo,
            "COMMIT_INTENT",
            {
                "actor": lock.actor,
                "harness": lock.harness,
                "task_id": lock.task_id,
                "run_id": lock.run_id,
                "thread_id": lock.thread_id,
                "session_id": lock.session_id,
                "mutation_lease_id": lock.mutation_lease_id,
                "pid": lock.metadata.get("pid", pas.PROVENANCE_UNKNOWN),
                "base_head": base.strip(),
                "owned_paths": list(lock.scope),
                "staged_paths": staged,
            },
            lock_root=lock.lock_root,
        )
        print(f"CANONICAL_PRE_COMMIT_ALLOWED: intent={event}")
        return 0
    except Exception as exc:  # noqa: BLE001
        return _print_error(exc)


def cmd_post_commit(args: argparse.Namespace) -> int:
    try:
        repo = resolve_repo(args.repo)
        lock = _load_active_lock(repo, args, require_token=True)
        rc, head = pas.git(repo, "rev-parse", "HEAD")
        if rc != 0:
            raise RuntimeError("COMMIT_RESULT_HEAD_UNAVAILABLE")
        head = head.strip()
        if pas._owned_commit_receipt(repo, head, lock.lock_root / "receipts"):
            print(f"CANONICAL_POST_COMMIT_ALREADY_RECEIPTED: {head}")
            return 0
        intents = _intent_events(lock)
        if not intents:
            pas.write_mutation_event(
                repo,
                "COMMIT_WITHOUT_INTENT",
                {"run_id": lock.run_id, "mutation_lease_id": lock.mutation_lease_id, "result_head": head},
                lock_root=lock.lock_root,
            )
            raise RuntimeError("COMMITTED_WITHOUT_INTENT")
        _path, intent = intents[0]
        changed = pas._commit_affected_files(repo, head)
        foreign = sorted(path for path in changed if not pas._path_in_scope(path, lock.scope))
        if foreign:
            pas.write_mutation_event(
                repo,
                "SCOPE_VIOLATION",
                {"run_id": lock.run_id, "result_head": head, "foreign_paths": foreign},
                lock_root=lock.lock_root,
            )
            raise RuntimeError(f"ABORT_FOREIGN_COMMITTED_PATH: {foreign}")
        receipt = pas.write_mutation_receipt(
            lock,
            base=str(intent.get("base_head", pas.PROVENANCE_UNKNOWN)),
            result="COMMITTED",
            staged=[str(item) for item in intent.get("staged_paths", [])],
            changed=changed,
            commit=head,
            base_head=str(intent.get("base_head", pas.PROVENANCE_UNKNOWN)),
            result_head=head,
            operation="owned-commit",
        )
        if not pas.validate_mutation_receipt(receipt, repo, commit=head):
            raise RuntimeError(f"COMMITTED_WITHOUT_VALID_RECEIPT: {receipt}")
        print(f"CANONICAL_POST_COMMIT_RECEIPT: {receipt}")
        return 0
    except Exception as exc:  # noqa: BLE001
        return _print_error(exc)


def cmd_pre_push(args: argparse.Namespace) -> int:
    try:
        repo = resolve_repo(args.repo)
        if not args.remote:
            raise ValueError("PRE_PUSH_REMOTE_MISSING")
        lines = [line.strip().split() for line in sys.stdin.read().splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("PRE_PUSH_NO_REF")
        auth_path = os.environ.get(PUSH_AUTH_ENV)
        if not auth_path:
            raise pas.MutationOwnershipError(
                "PUSH_AUTHORIZATION_REQUIRED",
                "PUSH_AUTHORIZATION_REQUIRED: raw push is not a governed mutation",
            )
        lock = _load_active_lock(repo, args, scope=["git-history"])
        try:
            auth_doc = json.loads(Path(auth_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            auth_doc = {}
        push_ref_mode = auth_doc.get("schema") == pas.PUSH_REF_AUTHORIZATION_SCHEMA
        for fields in lines:
            if len(fields) != 4:
                raise RuntimeError("PRE_PUSH_INVALID_REF_LINE")
            local_ref, local_sha, remote_ref, remote_sha = fields
            if local_ref == "(delete)" or local_sha == "0" * 40:
                raise RuntimeError("PRE_PUSH_DELETE_DENIED")
            if push_ref_mode:
                ok, detail = pas.validate_push_ref_authorization(
                    auth_path,
                    repo,
                    remote=args.remote,
                    local_ref=local_ref,
                    local_sha=local_sha,
                    remote_ref=remote_ref,
                    remote_sha=remote_sha,
                )
                if not ok:
                    raise RuntimeError(detail)
                continue
            if remote_ref != f"refs/heads/{args.branch}":
                raise RuntimeError("PRE_PUSH_TARGET_MISMATCH")
            if remote_sha != "0" * 40:
                if auth_doc.get("expected_remote") != remote_sha:
                    raise RuntimeError("PUSH_REMOTE_CHANGED")
            ok, detail = pas.validate_push_authorization(
                auth_path,
                repo,
                remote=args.remote,
                branch=args.branch,
                commit=local_sha,
            )
            if not ok:
                raise RuntimeError(detail)
        print(f"CANONICAL_PRE_PUSH_ALLOWED: {args.remote}/{args.branch} lease={lock.mutation_lease_id}")
        return 0
    except Exception as exc:  # noqa: BLE001
        return _print_error(exc)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="canonical_writer")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo")
        p.add_argument("--lock-root")
        p.add_argument("--harness")
        p.add_argument("--lease-id")
        p.add_argument("--run-id")

    p = sub.add_parser("preflight")
    common(p)
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--scope", action="append", default=[])
    p.add_argument("--scope-contract")
    p.add_argument("--git-operation", choices=["staging", "commit", "push"])
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("with-lease")
    common(p)
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--scope", action="append", default=[])
    p.add_argument("--scope-contract")
    p.add_argument("--operation", default="content-mutation")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_with_lease)

    p = sub.add_parser("hold")
    common(p)
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--scope", action="append", default=[])
    p.add_argument("--scope-contract")
    p.add_argument("--operation", default="content-mutation")
    p.add_argument("--seconds", type=float, default=30.0)
    p.set_defaults(func=cmd_hold)

    p = sub.add_parser("commit")
    common(p)
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--scope-contract")
    p.add_argument("--message", required=True)
    p.add_argument("--allow-foreign-dirty", action="store_true")
    p.add_argument("--no-validate", action="store_true")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("push")
    common(p)
    p.add_argument("--remote", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--timeout", type=int, default=300)
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("push-ref")
    common(p)
    p.add_argument("--remote", required=True)
    p.add_argument("--source-ref", required=True)
    p.add_argument("--dest-ref", required=True)
    p.add_argument("--expected-remote", default="")
    p.add_argument("--timeout", type=int, default=300)
    p.set_defaults(func=cmd_push_ref)

    p = sub.add_parser("pre-commit")
    common(p)
    p.set_defaults(func=cmd_pre_commit)

    p = sub.add_parser("post-commit")
    common(p)
    p.set_defaults(func=cmd_post_commit)

    p = sub.add_parser("pre-push")
    common(p)
    p.add_argument("remote")
    p.add_argument("url", nargs="?")
    p.add_argument("--branch", default=os.environ.get("PERSONAL_AI_PUSH_BRANCH", ""))
    p.set_defaults(func=cmd_pre_push)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "pre-push" and not args.branch:
        auth_path = os.environ.get(PUSH_AUTH_ENV)
        if auth_path and Path(auth_path).is_file():
            try:
                args.branch = json.loads(Path(auth_path).read_text(encoding="utf-8")).get("branch", "")
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
