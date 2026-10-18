"""Retire only recorded clean runtime merges, retaining commits and delivery proof."""

import json
from pathlib import Path
import subprocess

from .adapters import command
from .cleanup_orca import ref_value
from .maintenance import write_json
from .store import Conflict, fingerprint
from .verification_artifacts import checkout_identity


def creation_path(store, workspace):
    return store.path / "clean-integrations" / (fingerprint(str(workspace)) + ".json")


def observe(workspace):
    identity = checkout_identity(workspace)
    if command(["git", "status", "--porcelain", "--untracked-files=no"], workspace):
        raise Conflict("Clean integration has tracked file or index changes")
    if any(
        (Path(identity["gitdir"]) / name).exists()
        for name in (
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
        )
    ):
        raise Conflict("Clean integration has an unfinished user operation")
    return {
        "checkout": identity,
        "head": command(["git", "rev-parse", "HEAD"], workspace),
        "branch": command(["git", "rev-parse", "--symbolic-full-name", "HEAD"], workspace),
    }


def commit(repo, head):
    fields = command(["git", "show", "-s", "--format=%T %P", head], repo).split()
    return fields[0], fields[1:]


def capture(store, track, task, workspace, base, created, *, candidate_head=None, repo_name=None):
    """Called immediately after the host merge, before verification or worker access."""
    candidate_head = candidate_head or track["head"]
    target = observe(workspace)
    tree, parents = commit(workspace, target["head"])
    if (
        target["checkout"] != created
        or target["branch"] != "HEAD"
        or parents != [base, candidate_head]
        or observe(workspace) != target
    ):
        raise Conflict("Clean integration changed during creation")
    record = {
        "version": 1,
        "track": track["id"],
        "request": track["request"],
        "revision": track["revision"],
        "task": task["id"],
        "attempt": task["attempt"],
        "generation": task["generation"],
        "target": target,
        "tree": tree,
        "parents": parents,
        **({"repository": repo_name} if repo_name is not None else {}),
    }
    path = creation_path(store, workspace)
    if path.exists() and json.loads(path.read_text()) != record:
        raise Conflict("Clean integration creation evidence already differs")
    write_json(path, record)
    if json.loads(path.read_text()) != record:
        raise Conflict("Clean integration creation evidence was not persisted")


def is_ancestor(repo, ancestor, descendant):
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode not in (0, 1):
        raise Conflict("Cannot establish integration ancestry: " + result.stderr.strip())
    return result.returncode == 0


def candidates(connection, track, tree, repo_name=None):
    # A prior repaired candidate can have this tree even if later work changed it.
    heads = [track["head"]]
    rows = connection.execute(
        "SELECT body FROM events WHERE track=? AND type='verification.recorded'",
        (track["id"],),
    )
    for row in rows:
        record = json.loads(row["body"])
        if (
            record.get("tree") == tree
            and record.get("head")
            and record.get("repository") == repo_name
        ):
            heads.append(record["head"])
    return list(dict.fromkeys(heads))


def landing_head(track, repo_name=None):
    landing = json.loads(track["landing"]) if track["landing"] else {}
    if repo_name is not None:
        return landing.get("repositories", {}).get(repo_name, {}).get("merged")
    return landing.get("merged")


def check(
    store,
    track,
    workspace,
    target,
    proof,
    remote,
    connection,
    *,
    require_ref=True,
    candidate_head=None,
    repo_name=None,
):
    """Also works after removal: never infer preservation from directory absence."""
    candidate_head = candidate_head or track["head"]
    source = proof["source"]
    path = creation_path(store, workspace)
    if not path.exists() or json.loads(path.read_text()) != source:
        raise Conflict("Clean integration creation evidence is missing or changed")
    attempt = connection.execute(
        "SELECT a.generation,t.track,t.kind,t.input_revision FROM attempts a "
        "JOIN tasks t ON t.id=a.task WHERE a.id=? AND t.id=?",
        (source["attempt"], source["task"]),
    ).fetchone()
    expected_path = store.path / "integrations" / source["attempt"]
    if repo_name is not None:
        expected_path = expected_path.with_name(expected_path.name + "-" + repo_name)
    if (
        proof.get("version") != 1
        or source.get("version") != 1
        or any(source[key] != track[key] for key in ("request", "revision"))
        or source["track"] != track["id"]
        or source.get("repository") != repo_name
        or Path(workspace) != expected_path
        or target != source["target"]
        or target["checkout"]["path"] != str(expected_path)
        or target["branch"] != "HEAD"
        or len(source["parents"]) != 2
        or attempt is None
        or attempt["track"] != track["id"]
        or attempt["kind"] != "land"
        or attempt["generation"] != source["generation"]
        or attempt["input_revision"] != source["revision"]
    ):
        raise Conflict("Clean integration ownership or target changed")
    repo = target["checkout"]["common"]
    if commit(repo, target["head"]) != (source["tree"], source["parents"]):
        raise Conflict("Clean integration tree or parents changed")
    if proof["candidate"] not in candidates(connection, track, source["tree"], repo_name=repo_name):
        raise Conflict("Equivalent candidate execution evidence is missing")
    if commit(repo, proof["candidate"])[0] != source["tree"]:
        raise Conflict("Clean integration tree differs from the included candidate")
    if not is_ancestor(repo, proof["candidate"], candidate_head):
        raise Conflict("Equivalent candidate is not included in the delivered candidate")
    for parent in source["parents"]:
        if not is_ancestor(repo, parent, proof["remoteHead"]):
            raise Conflict(
                "Clean integration parent is not included in confirmed remote: " + parent
            )
    if (
        proof["landing"] != landing_head(track, repo_name)
        or not is_ancestor(repo, proof["candidate"], proof["remoteHead"])
        or not is_ancestor(repo, proof["remoteHead"], remote)
    ):
        raise Conflict("Clean integration remote inclusion evidence changed")
    if proof["ref"] != "refs/todo-flow/retired-integrations/" + fingerprint(source):
        raise Conflict("Clean integration preservation ref identity changed")
    value = ref_value(repo, proof["ref"])
    if (require_ref or value is not None) and value != target["head"]:
        raise Conflict("Clean integration preservation ref is missing or changed")


def plan(
    store,
    track,
    workspace,
    target,
    remote,
    connection,
    previous=None,
    *,
    candidate_head=None,
    repo_name=None,
):
    candidate_head = candidate_head or track["head"]
    path = creation_path(store, workspace)
    if not path.exists():
        raise Conflict("No clean integration creation evidence; preserve unlanded HEAD")
    source = json.loads(path.read_text())
    proof = previous
    if proof is None:
        repo = target["checkout"]["common"]
        candidate = next(
            (
                head
                for head in candidates(connection, track, source["tree"], repo_name=repo_name)
                if commit(repo, head)[0] == source["tree"]
                and is_ancestor(repo, head, candidate_head)
            ),
            None,
        )
        if candidate is None:
            raise Conflict("Clean integration tree differs from every included candidate")
        proof = {
            "version": 1,
            "source": source,
            "candidate": candidate,
            "landing": landing_head(track, repo_name),
            "remoteHead": remote,
            "ref": "refs/todo-flow/retired-integrations/" + fingerprint(source),
        }
    check(
        store,
        track,
        workspace,
        target,
        proof,
        remote,
        connection,
        require_ref=False,
        candidate_head=candidate_head,
        repo_name=repo_name,
    )
    if observe(workspace) != target:
        raise Conflict("Clean integration checkout changed before removal")
    return proof


def retain(proof):
    source = proof["source"]
    repo, head = source["target"]["checkout"]["common"], source["target"]["head"]
    value = ref_value(repo, proof["ref"])
    if value is None:
        command(
            ["git", "update-ref", "--no-deref", proof["ref"], head, "0" * len(head)],
            repo,
        )
    elif value != head:
        raise Conflict("Clean integration preservation ref changed")
    if ref_value(repo, proof["ref"]) != head:
        raise Conflict("Clean integration commit preservation is unconfirmed")
