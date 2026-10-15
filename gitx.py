#!/usr/bin/env python3
"""gitx: lightweight git sync over SSH.

A small CLI that performs clone / fetch / pull / push / sync through
libgit2 (pygit2) using the standard SSH transport, without invoking the
git command-line tool. Credentials come from ~/.ssh: the key referenced
by the github.com entry in the ssh config when present, otherwise
id_ed25519 / id_rsa, with a final fallback to the ssh-agent.

Pull integrates by fast-forward when possible and by merge otherwise
(there is no rebase; merge covers out-of-order edits from multiple
machines the same way).

Usage:
    sh install.sh                       set up venv + pygit2 + PATH launcher
    gitx clone <repo> [dir]             clone over SSH (owner/name or URL)
    gitx fetch [remote]                 download updates (default: origin)
    gitx pull [remote]                  fetch and integrate into the branch
    gitx push [remote]                  push the current branch
    gitx sync [remote]                  fetch + integrate + push in one step
    gitx status                         show branch, tracking and workdir state

Examples:
    sh install.sh
    gitx clone omgseven/notes
    gitx sync
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    import pygit2
    from pygit2.enums import CheckoutStrategy
except ImportError:  # install command must run before pygit2 exists
    pygit2 = None
    CheckoutStrategy = None

__version__ = "0.1.0"

# status bits that count as "uncommitted tracked changes"
TRACKED_CHANGE_BITS = (
    pygit2.GIT_STATUS_INDEX_NEW | pygit2.GIT_STATUS_INDEX_MODIFIED
    | pygit2.GIT_STATUS_INDEX_DELETED | pygit2.GIT_STATUS_INDEX_RENAMED
    | pygit2.GIT_STATUS_INDEX_TYPECHANGE
    | pygit2.GIT_STATUS_WT_MODIFIED | pygit2.GIT_STATUS_WT_DELETED
    | pygit2.GIT_STATUS_WT_RENAMED | pygit2.GIT_STATUS_WT_TYPECHANGE
) if pygit2 is not None else 0

# safe checkout: update files, keep protection against clobbering local edits
CHECKOUT_SAFE = (int(CheckoutStrategy.SAFE) | int(CheckoutStrategy.RECREATE_MISSING)) \
    if pygit2 is not None else 0

GIT_ERROR = pygit2.GitError if pygit2 is not None else None
KEY_NAMES = ("github_ssh", "id_ed25519", "id_rsa")
GITHUB_PREFIX = "ssh://git@github.com/"


class GitxError(Exception):
    """Expected, user-facing failure."""


def err(message):
    raise GitxError(message)


def short(oid):
    return str(oid)[:7]


def require_pygit2():
    if pygit2 is None:
        err("pygit2 is not installed; run 'gitx install' to repair the setup")


# --------------------------------------------------------------------------
# SSH credentials and progress


_REMOTE_CALLBACKS_BASE = pygit2.RemoteCallbacks if pygit2 is not None else object


class GitxCallbacks(_REMOTE_CALLBACKS_BASE):
    """SSH credentials, transfer progress and push result tracking.

    Credentials are tried in order: the ssh keys found under ~/.ssh
    (github_ssh first, matching the ssh config), then the ssh-agent.
    libgit2 calls the credentials callback again after a rejected
    attempt, so the index keeps advancing until a credential is
    accepted or the list is exhausted.
    """

    def __init__(self):
        super().__init__()
        home = Path.home()
        self._keys = []
        for name in KEY_NAMES:
            priv = home / ".ssh" / name
            pub = home / ".ssh" / f"{name}.pub"
            if priv.exists() and pub.exists():
                self._keys.append((str(pub), str(priv)))
        self._attempt = 0
        self._progress_shown = False
        # refs the remote rejected during push, as (refname, message)
        self.rejected = []

    def credentials(self, url, username_from_url, allowed_types):
        index = self._attempt
        self._attempt += 1
        if index < len(self._keys):
            pub, priv = self._keys[index]
            return pygit2.Keypair(username_from_url or "git", pub, priv, None)
        if index == len(self._keys):
            return pygit2.KeypairFromAgent("git")
        raise GitxError("authentication failed: no usable ssh key found "
                        "(checked ~/.ssh and the ssh-agent)")

    def transfer_progress(self, stats):
        if stats.total_objects:
            percent = int(100 * stats.received_objects / stats.total_objects)
            print(f"\r  receiving objects: {percent}% "
                  f"({stats.received_objects}/{stats.total_objects})",
                  end="", file=sys.stderr)
            self._progress_shown = True

    def push_update_reference(self, refname, message):
        if message is not None:
            self.rejected.append((refname, message))

    def finish(self):
        if self._progress_shown:
            print(file=sys.stderr)


# --------------------------------------------------------------------------
# Repository helpers


def open_repo():
    require_pygit2()
    found = pygit2.discover_repository(os.getcwd())
    if not found:
        err("not inside a git repository")
    return pygit2.Repository(found)


def get_remote(repo, name):
    try:
        return repo.remotes[name]
    except (KeyError, IndexError):
        err(f"remote '{name}' not found in this repository")


def resolve_url(target):
    if "://" in target or target.startswith("git@") \
            or target.startswith("/"):
        return target
    if target.count("/") == 1:
        return f"{GITHUB_PREFIX}{target}.git"
    err(f"cannot parse repository '{target}'; use owner/name or a full URL")


def current_branch(repo):
    """Return (branch_name, local_ref) for the checked-out branch."""
    if repo.head_is_unborn:
        err("this repository has no commits yet; create a commit first")
    if repo.head_is_detached:
        err("HEAD is detached; checkout a branch first")
    head = repo.head
    return head.shorthand, repo.lookup_reference(head.name)


def tracking_ref(repo, remote_name, branch_name):
    try:
        return repo.lookup_reference(f"refs/remotes/{remote_name}/{branch_name}")
    except KeyError:
        return None


def snapshot_remote_refs(repo, remote_name):
    prefix = f"refs/remotes/{remote_name}/"
    out = {}
    for name in repo.references:
        if name.startswith(prefix):
            out[name[len(prefix):]] = str(repo.references[name].target)
    return out


def ensure_no_tracked_changes(repo):
    dirty = sorted(p for p, f in repo.status().items() if f & TRACKED_CHANGE_BITS)
    if dirty:
        preview = ", ".join(dirty[:5]) + (" ..." if len(dirty) > 5 else "")
        err(f"work directory has uncommitted changes ({preview}); "
            "commit them before syncing")


def collect_conflicts(repo):
    names = set()
    try:
        entries = list(repo.index.conflicts or [])
    except (TypeError, ValueError):
        entries = []
    for entry in entries:
        for item in entry:
            if item is not None:
                names.add(item.path)
    return sorted(names)


def make_signature(repo):
    try:
        return repo.default_signature
    except GIT_ERROR:
        err("git user.name / user.email are not configured "
            "(needed to create merge commits)")


# --------------------------------------------------------------------------
# Network operations


def do_fetch(repo, remote_name, quiet=False):
    """Fetch all branches; report which remote-tracking refs changed."""
    remote = get_remote(repo, remote_name)
    refspecs = [str(r) for r in remote.fetch_refspecs] or \
        [f"+refs/heads/*:refs/remotes/{remote_name}/*"]
    before = snapshot_remote_refs(repo, remote_name)
    callbacks = GitxCallbacks()
    remote.fetch(refspecs, callbacks=callbacks)
    callbacks.finish()
    after = snapshot_remote_refs(repo, remote_name)

    changes = []
    for name in sorted(after):
        old, new = before.get(name), after[name]
        if old is None:
            changes.append((name, None, new))
        elif old != new:
            changes.append((name, old, new))
    if not quiet:
        if changes:
            for name, old, new in changes:
                if old is None:
                    print(f"  + {remote_name}/{name}: {short(new)} (new)")
                else:
                    print(f"  * {remote_name}/{name}: "
                          f"{short(old)} -> {short(new)}")
        else:
            print("  everything up to date")
    return changes


def integrate(repo, remote_name):
    """Integrate the remote-tracking branch into the current branch.

    Returns one of: "up-to-date", "ahead", "fast-forward", "merged".
    """
    branch_name, local_ref = current_branch(repo)
    local_oid = local_ref.target
    remote_ref = tracking_ref(repo, remote_name, branch_name)
    if remote_ref is None:
        err(f"remote branch '{remote_name}/{branch_name}' not found; "
            "push this branch first")
    remote_oid = remote_ref.target

    if local_oid == remote_oid:
        return "up-to-date"
    if repo.descendant_of(local_oid, remote_oid):
        return "ahead"

    if repo.descendant_of(remote_oid, local_oid):
        ensure_no_tracked_changes(repo)
        # update the worktree first; only then move the branch ref, so a
        # failed checkout leaves the repository untouched
        repo.checkout_tree(repo[remote_oid], strategy=CHECKOUT_SAFE)
        local_ref.set_target(remote_oid,
                             f"gitx: fast-forward to {short(remote_oid)}")
        return "fast-forward"

    # histories diverged: merge
    # repo.merge() writes the merged result into the work directory and
    # stages it in the index, so only the merge commit is left to create
    ensure_no_tracked_changes(repo)
    try:
        repo.merge(remote_oid)
        conflicts = collect_conflicts(repo)
        if conflicts:
            preview = ", ".join(conflicts[:8]) + (" ..." if len(conflicts) > 8 else "")
            err(f"merge would conflict on {len(conflicts)} file(s): {preview}\n"
                "resolve manually, then commit the result")
        tree_oid = repo.index.write_tree()
        signature = make_signature(repo)
        message = f"Merge remote-tracking branch '{remote_name}/{branch_name}' (gitx)"
        repo.create_commit(local_ref.name, signature, signature, message,
                           tree_oid, [local_oid, remote_oid])
    except BaseException:
        # roll back a half-done merge, leaving the repository as found
        repo.state_cleanup()
        repo.reset(local_oid, pygit2.GIT_RESET_HARD)
        raise
    return "merged"


def do_push(repo, remote_name):
    """Push the current branch; refuse non-fast-forward updates."""
    branch_name, local_ref = current_branch(repo)
    local_oid = local_ref.target
    refspec = f"refs/heads/{branch_name}:refs/heads/{branch_name}"
    remote = get_remote(repo, remote_name)
    tracking = tracking_ref(repo, remote_name, branch_name)
    if tracking is not None and tracking.target != local_oid \
            and not repo.descendant_of(local_oid, tracking.target):
        err(f"remote branch '{remote_name}/{branch_name}' has commits "
            "not present locally; run 'gitx sync' (or pull) first")

    callbacks = GitxCallbacks()
    try:
        remote.push([refspec], callbacks=callbacks)
    except GIT_ERROR as exc:
        message = str(exc)
        if "not present locally" in message \
                or "fast forward" in message.lower() \
                or "fetch first" in message.lower():
            err(f"push to '{remote_name}/{branch_name}' was rejected: the "
                "remote has commits not present locally; "
                "run 'gitx sync' first")
        raise
    callbacks.finish()
    if callbacks.rejected:
        refname, message = callbacks.rejected[0]
        err(f"push to '{refname}' was rejected: {message}")

    # verify the push actually landed (a rejected push may not raise)
    heads = {}
    for head in remote.list_heads(callbacks=GitxCallbacks()):
        heads[head.name] = str(head.oid)
    landed = heads.get(f"refs/heads/{branch_name}")
    if landed is None:
        err(f"remote branch '{branch_name}' was not created; "
            "push was rejected by the remote")
    if landed != str(local_oid):
        err(f"push to '{remote_name}/{branch_name}' was rejected "
            f"(remote tip is {short(landed)}); run 'gitx sync' first")

    # keep the remote-tracking ref in step
    tracking_name = f"refs/remotes/{remote_name}/{branch_name}"
    try:
        repo.lookup_reference(tracking_name).set_target(
            local_oid, "gitx: push")
    except KeyError:
        repo.references.create(tracking_name, local_oid)
    return local_oid


# --------------------------------------------------------------------------
# Commands


def cmd_install(_args):
    script = Path(__file__).resolve()
    base = script.parent
    venv_dir = base / ".venv"
    venv_py = (venv_dir / "Scripts" / "python.exe") if os.name == "nt" \
        else (venv_dir / "bin" / "python3")

    if not venv_py.exists():
        print(f"creating virtualenv at {venv_dir} ...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)],
                       check=True)

    probe = subprocess.run(
        [str(venv_py), "-c", "import pygit2; print(pygit2.__version__)"],
        capture_output=True, text=True)
    if probe.returncode != 0:
        print("installing pygit2 ...")
        install = subprocess.run(
            [str(venv_py), "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", "pygit2"],
            capture_output=True, text=True)
        if install.returncode != 0:
            err(f"pip install pygit2 failed:\n{install.stderr.strip()}")
        probe = subprocess.run(
            [str(venv_py), "-c", "import pygit2; print(pygit2.__version__)"],
            capture_output=True, text=True)
    version = probe.stdout.strip() or "unknown"

    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        launcher = bin_dir / "gitx.cmd"
        launcher.write_text(
            f'@echo off\r\n"{venv_py}" "{script}" %*\r\n', encoding="ascii")
    else:
        launcher = bin_dir / "gitx"
        launcher.write_text(f'#!/bin/sh\nexec "{venv_py}" "{script}" "$@"\n')
        launcher.chmod(0o755)

    print("gitx installed")
    print(f"  python:   {venv_py}")
    print(f"  pygit2:   {version}")
    print(f"  launcher: {launcher}")
    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"\nnote: {bin_dir} is not on your PATH. Add this line to your "
              "shell profile:\n  export PATH=\"$HOME/.local/bin:$PATH\"")
    return 0


def cmd_clone(args):
    require_pygit2()
    url = resolve_url(args.repo)
    dest = Path(args.dir) if args.dir else \
        Path(url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))
    if dest.exists() and any(dest.iterdir()):
        err(f"destination '{dest}' exists and is not empty")
    callbacks = GitxCallbacks()
    pygit2.clone_repository(url, str(dest), callbacks=callbacks)
    callbacks.finish()
    print(f"cloned {args.repo} into {dest}")
    return 0


def cmd_fetch(args):
    repo = open_repo()
    print(f"fetching {args.remote} ...")
    do_fetch(repo, args.remote)
    return 0


def cmd_pull(args):
    repo = open_repo()
    print(f"fetching {args.remote} ...")
    do_fetch(repo, args.remote)
    branch_name, _ = current_branch(repo)
    state = integrate(repo, args.remote)
    if state == "fast-forward":
        print(f"{branch_name}: fast-forwarded")
    elif state == "merged":
        print(f"{branch_name}: merged {args.remote}/{branch_name} "
              "(new merge commit created; push when ready)")
    elif state == "ahead":
        print(f"{branch_name}: local is ahead of {args.remote}; nothing "
              "to integrate")
    else:
        print(f"{branch_name}: already up to date")
    return 0


def cmd_push(args):
    repo = open_repo()
    branch_name, _ = current_branch(repo)
    print(f"pushing {branch_name} to {args.remote} ...")
    oid = do_push(repo, args.remote)
    print(f"  {branch_name} -> {args.remote}/{branch_name} ({short(oid)})")
    return 0


def cmd_sync(args):
    repo = open_repo()
    branch_name, _ = current_branch(repo)
    print(f"fetching {args.remote} ...")
    do_fetch(repo, args.remote)
    state = integrate(repo, args.remote)
    if state == "fast-forward":
        print(f"{branch_name}: fast-forwarded")
    elif state == "merged":
        print(f"{branch_name}: merged {args.remote}/{branch_name}")
    elif state == "ahead":
        print(f"{branch_name}: local commits to push")
    # re-read the branch tip: integrate may have moved it
    local_oid = repo.lookup_reference(f"refs/heads/{branch_name}").target
    tracking = tracking_ref(repo, args.remote, branch_name)
    if tracking is not None and tracking.target == local_oid:
        print(f"{branch_name}: already in sync with {args.remote}")
        return 0
    if tracking is None or repo.descendant_of(local_oid, tracking.target):
        print(f"pushing {branch_name} to {args.remote} ...")
        oid = do_push(repo, args.remote)
        print(f"  {branch_name} -> {args.remote}/{branch_name} ({short(oid)})")
        return 0
    err("unexpected repository state after integration; "
        f"local {short(local_oid)} vs remote "
        f"{short(tracking.target)}")


def cmd_status(_args):
    repo = open_repo()
    if repo.head_is_unborn:
        print("On branch: (no commits yet)")
        return 0
    if repo.head_is_detached:
        print(f"HEAD detached at {short(repo.head.target)}")
        return 0
    head = repo.head
    branch_name = head.shorthand
    print(f"On branch {branch_name}")
    unknown_remote = True
    for remote in repo.remotes:
        tracking = tracking_ref(repo, remote.name, branch_name)
        if tracking is None:
            continue
        unknown_remote = False
        ahead, behind = repo.ahead_behind(head.target, tracking.target)
        print(f"Tracking {remote.name}/{branch_name} "
              f"(ahead {ahead}, behind {behind})")
    if unknown_remote:
        print(f"No remote branch '{branch_name}' on any configured remote")
    status = repo.status()
    changed = sum(1 for f in status.values() if f & TRACKED_CHANGE_BITS)
    untracked = sum(1 for f in status.values() if f & pygit2.GIT_STATUS_WT_NEW)
    if changed or untracked:
        print(f"Workdir: {changed} changed, {untracked} untracked file(s)")
    else:
        print("Workdir: clean")
    return 0


# --------------------------------------------------------------------------
# Entry point


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gitx",
        description="Lightweight git sync over SSH (libgit2 based; "
                    "no git CLI required).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  sh install.sh\n"
               "  gitx clone omgseven/notes\n"
               "  gitx sync")
    parser.add_argument("--version", action="version",
                        version=f"gitx {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("install",
                       help="set up the venv, pygit2 and a PATH launcher")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("clone", help="clone a repository over SSH")
    p.add_argument("repo", help="owner/name or a full URL")
    p.add_argument("dir", nargs="?",
                   help="target directory (default: the repository name)")
    p.set_defaults(func=cmd_clone)

    p = sub.add_parser("fetch", help="download updates from the remote")
    p.add_argument("remote", nargs="?", default="origin")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("pull",
                       help="fetch and integrate (fast-forward, else merge)")
    p.add_argument("remote", nargs="?", default="origin")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("push", help="push the current branch")
    p.add_argument("remote", nargs="?", default="origin")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("sync",
                       help="fetch + integrate + push in one step")
    p.add_argument("remote", nargs="?", default="origin")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("status",
                       help="show branch, tracking and workdir state")
    p.set_defaults(func=cmd_status)
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GitxError as exc:
        print(f"gitx: error: {exc}", file=sys.stderr)
        return 1
    except GIT_ERROR as exc:
        print(f"gitx: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
