"""Reconcile owned Orca deletion with local Git, retaining branch recovery evidence."""

import os
from pathlib import Path
import subprocess

from .adapters import command
from . import managed_workspace as managed
from .orca_workspace import validate_workspace
from .store import Conflict, fingerprint
from .workspace_creation import WorkspaceCreationGate


def owner(store, track, value, repo, *, connection=None):
    """Only a bound creation receipt can authorize an external candidate path."""
    if value != track["workspace"]:
        return None
    gate = WorkspaceCreationGate(store.path, track["id"])
    if not managed._exists(managed.receipt_path(gate)):
        gate.require_clear()
        return None
    receipt, intent = managed.ownership(store, track, repo, connection=connection)
    observation = receipt["observation"]
    if (
        observation["path"] != value
        or observation["branch"] != "refs/heads/" + track["branch"]
        or observation["hostId"] != "local"
    ):
        raise Conflict("Managed ownership does not match the registered candidate")
    return {
        "ownership": fingerprint(receipt),
        "cli": intent["argv"][0],
        "observation": observation,
    }


def ref_value(repo, ref):
    rows = command(
        ["git", "for-each-ref", "--format=%(refname) %(objectname) %(symref)", ref], repo
    )
    for row in rows.splitlines():
        fields = row.split()
        if fields[0] == ref:
            if len(fields) != 2:
                raise Conflict("Cleanup branch or backup became a symbolic ref")
            return fields[1]
    return None


class OrcaCleanup:
    """Caller holds track, landing and Git locks and has checked delivery authority."""

    def __init__(self, store, track, repo, value, item, evidence, *, connection=None):
        self.store, self.track, self.repo = store, track, repo
        self.connection = connection
        self.value, self.item, self.evidence = value, item, evidence
        self.old = evidence["observation"]
        if item.get("orca", evidence) != evidence:
            raise Conflict("Orca cleanup ownership changed")
        item["orca"] = evidence

    def call(self, args):
        return managed._call(self.evidence["cli"], [*args, "--json"], self.repo)

    def inspect(self):
        """Require complete local coverage; never infer absence from a failed show."""
        from .cleanup import local_target, registered_worktrees

        if (
            owner(self.store, self.track, self.value, self.repo, connection=self.connection)
            != self.evidence
        ):
            raise Conflict("Orca cleanup ownership changed")
        status = self.call(["status"])
        runtime = status["result"]["runtime"]
        if (
            status["result"]["target"].get("kind") != "local"
            or runtime.get("state") != "ready"
            or runtime.get("reachable") is not True
            or not runtime.get("runtimeId")
        ):
            raise Conflict("Local Orca runtime cannot be verified")
        repo = self.call(["repo", "show", "--repo", "id:" + self.old["repoId"]])
        shown_repo = repo["result"]["repo"]
        if (
            shown_repo.get("id") != self.old["repoId"]
            or shown_repo.get("kind") != "git"
            or Path(shown_repo["path"]).resolve(strict=True) != Path(self.old["repo_path"])
            or command(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], self.repo
            )
            != self.old["common_dir"]
        ):
            raise Conflict("Orca repository ownership changed")
        inventory = self.call(["worktree", "ps", "--limit", "10000"])["result"]
        rows, scope = inventory.get("worktrees"), inventory.get("hostScope", {})
        if (
            not isinstance(rows, list)
            or not all(isinstance(row, dict) for row in rows)
            or inventory.get("truncated") is not False
            or type(inventory.get("totalCount")) is not int
            or inventory["totalCount"] != len(rows)
            or "local" not in scope.get("hostIds", [])
            or "local" in scope.get("omittedHostIds", [])
        ):
            raise Conflict("Orca inventory is incomplete; local absence is unconfirmed")
        matches = [
            row
            for row in rows
            if row.get("worktreeId") == self.old["id"]
            or (
                row.get("hostId") == "local"
                and (
                    row.get("path") == self.value
                    or row.get("worktreeInstanceId") == self.old["instanceId"]
                )
            )
        ]
        exists = os.path.lexists(self.value)
        registered = str(Path(self.value).resolve()) in registered_worktrees(self.repo)
        if not matches:
            if exists or registered:
                raise Conflict("Git and Orca inventories disagree")
            target = self.item.get("target")
            if target and os.path.lexists(target["checkout"]["gitdir"]):
                raise Conflict("Checkout metadata remains after removal")
            return False
        if len(matches) != 1:
            raise Conflict("Orca target identity is ambiguous")
        row = matches[0]
        expected = {
            "worktreeId": self.old["id"],
            "worktreeInstanceId": self.old["instanceId"],
            "repoId": self.old["repoId"],
            "hostId": "local",
            "path": self.value,
            "branch": self.old["branch"],
            "workspaceKind": "git",
            "isMainWorktree": False,
        }
        if any(row.get(key) != value for key, value in expected.items()):
            raise Conflict("Orca target identity changed")
        if not exists or not registered:
            raise Conflict("Git and Orca inventories disagree")
        target = self.item.get("target")
        if target is not None and local_target(self.value) != target:
            raise Conflict("Cleanup target identity, branch or HEAD changed")
        checked = validate_workspace(
            status,
            repo,
            self.call(["worktree", "show", "--worktree", "id:" + self.old["id"]]),
            expected=managed._expectation(self.old, self.track["head"]),
        )
        if checked["common_dir"] != self.old["common_dir"]:
            raise Conflict("Orca repository anchor changed")
        agents = row.get("agents")
        if (
            type(row.get("liveTerminalCount")) is not int
            or row["liveTerminalCount"] != 0
            or row.get("hasAttachedPty") is not False
            or row.get("isActive") is not False
            or row.get("status") != "inactive"
            or row.get("childWorktreeIds") != []
            or not isinstance(agents, list)
            or any(not isinstance(agent, dict) or agent.get("state") != "done" for agent in agents)
        ):
            raise Conflict("Orca workspace has active or unconfirmed sessions or children")
        terminals = self.call(["terminal", "list", "--worktree", "id:" + self.old["id"]])[
            "result"
        ].get("terminals")
        if terminals != []:
            raise Conflict("Orca terminals remain; preserve their workspace")
        self.branches(absent=False, dry_run=True)
        return True

    def backup_ref(self):
        target = self.item.get("target")
        if (
            not target
            or target["branch"] != self.old["branch"]
            or target["head"] != self.track["head"]
        ):
            raise Conflict("Branch backup has no matching deletion target")
        return "refs/todo-flow/cleanup/" + fingerprint([self.evidence, target])

    def branches(self, *, absent, dry_run):
        branch, head = self.old["branch"], self.track["head"]
        current = ref_value(self.repo, branch)
        self.item["branchPreserved"] = current == head
        if current == head:
            return
        if current is not None:
            raise Conflict("Preserved branch changed; cleanup will not overwrite it")
        if (
            not absent
            or not self.item.get("orcaRemovalIntent")
            or self.item.get("branchBackup") != self.backup_ref()
            or ref_value(self.repo, self.backup_ref()) != head
        ):
            raise Conflict("Branch is missing without confirmed cleanup recovery evidence")
        if dry_run:
            raise Conflict("Branch recovery requires cleanup without --dry-run")
        # Compare-and-create: never overwrite a user's replacement branch.
        command(["git", "update-ref", "--no-deref", branch, head, "0" * len(head)], self.repo)
        if ref_value(self.repo, branch) != head:
            raise Conflict("Branch restoration is unconfirmed")
        self.item["branchPreserved"] = True

    def remove(self, save):
        if self.item.get("orcaRemovalIntent"):
            raise Conflict("Previous Orca removal remains unconfirmed; do not dispatch it again")
        self.inspect()
        backup, head = self.backup_ref(), self.track["head"]
        self.item["branchBackup"] = backup
        save()
        value = ref_value(self.repo, backup)
        if value is None:
            command(["git", "update-ref", "--no-deref", backup, head, "0" * len(head)], self.repo)
        elif value != head:
            raise Conflict("Branch recovery ref changed")
        # Preserve the commit independently before Orca's documented branch deletion.
        # Keep this ref and the execution records after success and across interruption.
        self.inspect()
        self.item["orcaRemovalIntent"] = True
        self.item["branchPreserved"] = False
        save()
        try:
            self.call(["worktree", "rm", "--worktree", "id:" + self.old["id"]])
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            self.item["orcaRemovalError"] = str(error)
        # An RPC result is not a deletion receipt. Requery both inventories even
        # after timeout. If effects are uncertain, leave the intent for recovery.
        if self.inspect():
            raise Conflict("Orca removal is unconfirmed; workspace is still present")
        self.branches(absent=True, dry_run=False)
        self.item.pop("orcaRemovalError", None)
