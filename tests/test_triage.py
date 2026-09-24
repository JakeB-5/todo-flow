import json
import shutil
import time
import unittest
from unittest.mock import patch

import test_flow
from test_flow import DOC
from todo_flow import documents
from todo_flow.adapters import command
from todo_flow.engine import Engine
from todo_flow.store import Conflict, encode
from todo_flow.triage import Triage, cleared


def disposition(source, action, scope="out-of-scope", **extra):
    return {
        "source": source,
        "action": action,
        "scope": scope,
        "confirmed": True,
        "observation": source,
        "evidence": "Observed landed calc.py and its tests",
        "reason": "Concrete assessment of the current result",
        **extra,
    }


class TriageTests(unittest.TestCase):
    setUp = test_flow.IntegrationTests.setUp
    tearDown = test_flow.IntegrationTests.tearDown

    @unittest.skipUnless(shutil.which("rg"), "Requires real ripgrep")
    def test_duplicate_search_uses_current_rich_documents_not_revision_folders(self):
        for mode, suffix in (("html", "html"), ("markdown", "md")):
            path = self.root / ("candidate." + suffix)
            for revision, goal in ((1, "retired-obligation"), (2, "current-obligation")):
                doc = {**DOC, "id": "candidate-" + mode, "goal": goal}
                metadata = json.dumps(doc)
                source = (
                    '<script type="application/json" id="todo-flow-track">'
                    + metadata
                    + "</script><p>Reviewable plan</p>"
                    if mode == "html"
                    else "---\n" + metadata + "\n---\n# Reviewable plan\n"
                )
                path.write_text(source)
                self.s.register(documents.load(path), expected=revision - 1)
        triage = Triage(Engine(self.s))
        current = triage.candidates(DOC, [], terms=["current-obligation"])
        self.assertEqual(
            [row["id"] for row in current["candidates"]],
            ["candidate-html", "candidate-markdown"],
        )
        self.assertFalse(current["truncated"])
        retired = triage.candidates(DOC, [], terms=["retired-obligation"])
        self.assertEqual(retired["candidates"], [])

    def landed(self):
        self.s.start("addition")
        self.e = Engine(self.s)
        self.e.run(max_tasks=4)
        task = self.s.claim("triager")
        self.assertEqual(task["kind"], "triage")
        triage = Triage(self.e)
        return task, triage, triage.prepare(task)

    def test_dispositions_register_link_watch_and_close_without_starting_children(self):
        self.s.register({**DOC, "id": "existing", "goal": "A different future obligation"})
        task, triage, context = self.landed()
        child = {
            **DOC,
            "id": "new-followup",
            "title": "Future numeric API",
            "goal": "A separate numeric API",
        }
        items = [
            disposition("new:child", "new-track", registration=encode(child)),
            disposition("new:covered", "existing", target="existing"),
            disposition(
                "new:conditional",
                "watch",
                scope="uncertain",
                confirmed=False,
                trigger="new-runtime-version",
                next_action="Reproduce after runtime upgrade",
            ),
            disposition("new:fixed", "resolved"),
            disposition("new:false-alarm", "dismissed"),
        ]
        triage.apply(
            task, context, {"summary": "Each observation has a disposition", "triage": items}
        )
        self.e.run(max_tasks=2)
        self.assertEqual(self.s.track("addition")["status"], "done")
        self.assertEqual(self.s.track("new-followup")["control"], "idle")
        self.assertEqual(self.s.track("existing")["control"], "idle")
        self.assertTrue((self.s.path / "tracks/new-followup/track.html").exists())
        self.assertFalse(any(w["track"] != "addition" for w in self.s.snapshot()["tasks"]))
        receipt = json.loads(self.s.snapshot()["triages"][0]["body"])
        self.assertTrue(receipt["cleared"])
        self.assertEqual(len(receipt["items"]), 5)
        self.assertEqual(len(self.s.snapshot()["watches"]), 1)
        shutil.rmtree(self.s.path / ".cache")
        self.assertEqual(len(self.s.snapshot()["triages"]), 1)
        self.assertEqual(self.e.run(max_tasks=2), 0)

    def test_in_scope_repair_gets_fresh_branch_review_landing_and_triage(self):
        task, triage, context = self.landed()
        original = self.s.track("addition")
        triage.apply(
            task,
            context,
            {
                "summary": "Numeric addition must reject text",
                "triage": [
                    disposition(
                        "new:text-addition",
                        "repair",
                        "in-scope",
                        observation="Reject text operands for numeric addition",
                    )
                ],
            },
        )
        changed = self.s.track("addition")
        self.assertNotEqual(changed["branch"], original["branch"])
        self.assertIsNone(changed["review"])
        self.assertIsNone(changed["landing"])
        self.e.run(max_tasks=12)
        latest = self.s.track("addition")
        self.assertEqual(latest["status"], "done", encode(self.s.snapshot()["decisions"]))
        self.assertNotEqual(latest["head"], original["head"])
        self.assertNotEqual(
            json.loads(latest["review"])["attempt"], json.loads(original["review"])["attempt"]
        )
        self.assertIn(
            "TypeError", command(["git", "--git-dir", str(self.remote), "show", "main:calc.py"])
        )
        self.assertEqual(len(self.s.snapshot()["triages"]), 2)
        receipts = [json.loads(row["body"]) for row in self.s.snapshot()["triages"]]
        self.assertTrue(
            any(row["candidate"] == latest["head"] and row["cleared"] for row in receipts)
        )

    def test_disposition_batch_is_atomic_and_original_scope_cannot_escape(self):
        task, triage, context = self.landed()
        child = {**DOC, "id": "would-be-child"}
        with self.assertRaises(Conflict):
            triage.apply(
                task,
                context,
                {
                    "summary": "Invalid scope move",
                    "triage": [
                        disposition(
                            "new:escape", "new-track", "in-scope", registration=encode(child)
                        )
                    ],
                },
            )
        with self.assertRaises(ValueError):
            triage.apply(
                task,
                context,
                {
                    "summary": "Invalid second item",
                    "triage": [
                        disposition("new:child", "new-track", registration=encode(child)),
                        disposition("new:watch", "watch", "uncertain", confirmed=False),
                    ],
                },
            )
        self.assertEqual(len(self.s.snapshot()["tracks"]), 1)
        self.assertEqual(self.s.snapshot()["triages"], [])
        self.assertEqual(self.s.snapshot()["findings"], [])
        self.assertFalse((self.s.path / "tracks/would-be-child").exists())

    def test_watch_reassessment_and_coverage_requirement(self):
        with self.s.transaction() as c:
            c.execute(
                "INSERT INTO watches VALUES(?,?,?,?,?,?,?)",
                (
                    "w1",
                    "addition",
                    encode(
                        {
                            "observation": "old failure",
                            "reason": "unconfirmed",
                            "trigger": "land",
                            "next_action": "check",
                        }
                    ),
                    "open",
                    "land",
                    None,
                    time.time(),
                ),
            )
        task, triage, context = self.landed()
        with self.assertRaises(ValueError):
            triage.apply(task, context, {"summary": "Omitted source", "triage": []})
        triage.apply(
            task,
            context,
            {
                "summary": "Watch resolved on landed code",
                "triage": [disposition("watch-w1", "resolved", "in-scope")],
            },
        )
        self.assertEqual(self.s.snapshot()["watches"][0]["status"], "resolved")
        self.e.run(max_tasks=2)
        self.assertEqual(self.s.track("addition")["status"], "done")

    def test_wait_answer_resumes_triage_and_stale_claim_cannot_dispose(self):
        task, triage, context = self.landed()
        self.s.finish(
            task,
            {
                "summary": "Scope needs a product choice",
                "question": "Is the new behavior required?",
            },
        )
        decision = self.s.snapshot()["decisions"][0]
        self.s.answer(decision["id"], "Keep the existing scope")
        resumed = self.s.claim("successor")
        self.assertEqual(resumed["kind"], "triage")
        with self.assertRaises(Conflict):
            triage.apply(task, context, {"summary": "Late response", "triage": []})
        triage.apply(
            resumed,
            triage.prepare(resumed),
            {"summary": "No unresolved original obligations", "triage": []},
        )
        self.e.run(max_tasks=2)
        self.assertEqual(self.s.track("addition")["status"], "done")

    def test_new_finding_invalidates_clear_receipt_and_old_completion_cannot_skip(self):
        task, triage, context = self.landed()
        triage.apply(task, context, {"summary": "Clear at this observation", "triage": []})
        with self.s.transaction() as c:
            self.assertTrue(cleared(self.s, c, "addition"))
            c.execute(
                "INSERT INTO findings VALUES(?,?,?,?,?)",
                (
                    "late",
                    "addition",
                    encode({"observation": "late fact", "evidence": "new evidence"}),
                    "open",
                    time.time(),
                ),
            )
            self.assertFalse(cleared(self.s, c, "addition"))
        complete = self.s.claim("completion")
        result = self.e.complete(complete)
        self.assertIn("wait_for", result)
        self.assertEqual(self.s.track("addition")["status"], "open")

    def test_changed_remote_base_requeues_only_triage(self):
        task, triage, context = self.landed()
        with (
            patch.object(triage, "prepare", return_value=context),
            patch("todo_flow.triage.run_worker", return_value={"summary": "Clear", "triage": []}),
            patch("todo_flow.triage.command", return_value="changed-base\trefs/heads/main"),
        ):
            triage.run(task)
        self.assertEqual(self.s.snapshot()["triages"], [])
        self.assertEqual(self.s.claim("fresh")["kind"], "triage")

    def test_changed_findings_reassess_without_user_question(self):
        task, triage, context = self.landed()
        with self.s.transaction() as c:
            c.execute(
                "INSERT INTO findings VALUES(?,?,?,?,?)",
                (
                    "arrived",
                    "addition",
                    encode({"observation": "new observed fact", "evidence": "new record"}),
                    "open",
                    time.time(),
                ),
            )
        with (
            patch.object(triage, "prepare", return_value=context),
            patch(
                "todo_flow.triage.run_worker",
                return_value={"summary": "Previously clear", "triage": []},
            ),
            patch(
                "todo_flow.triage.command",
                return_value=context["triage_context"]["base"] + "\trefs/heads/main",
            ),
        ):
            triage.run(task)
        self.assertEqual(self.s.snapshot()["decisions"], [])
        self.assertEqual(self.s.snapshot()["triages"], [])
        fresh = self.s.claim("fresh")
        self.assertEqual(fresh["kind"], "triage")
        triage.apply(
            fresh,
            triage.prepare(fresh),
            {"summary": "New fact assessed", "triage": [disposition("arrived", "resolved")]},
        )
        self.e.run(max_tasks=2)
        self.assertEqual(self.s.track("addition")["status"], "done")

    def test_each_new_obligation_gets_a_fresh_completion_dependency(self):
        task, triage, context = self.landed()
        triage.apply(task, context, {"summary": "Clear", "triage": []})
        requested = []
        for index in range(2):
            complete = self.s.claim("completion")
            self.assertEqual(complete["kind"], "complete")
            id_ = f"late-{index}"
            with self.s.transaction() as c:
                c.execute(
                    "INSERT INTO findings VALUES(?,?,?,?,?)",
                    (
                        id_,
                        "addition",
                        encode({"observation": id_, "evidence": "new record"}),
                        "open",
                        time.time(),
                    ),
                )
            result = self.e.complete(complete)
            requested.append(result["wait_for"][0])
            self.s.finish(complete, result)
            follow = self.s.claim("triage")
            self.assertEqual(follow["id"], requested[-1])
            triage.apply(
                follow,
                triage.prepare(follow),
                {"summary": "New obligation assessed", "triage": [disposition(id_, "resolved")]},
            )
            self.e.reconcile()
        self.assertNotEqual(requested[0], requested[1])
        self.e.run(max_tasks=1)
        self.assertEqual(self.s.track("addition")["status"], "done")

    def test_remote_review_inline_and_discussion_are_sources(self):
        task, triage, context = self.landed()
        self.e.remote = object()
        self.e.config["github"] = "owner/fixture"
        t = self.s.track("addition")
        t["pr"] = 42
        pages = encode(
            [
                [
                    {
                        "id": 99,
                        "body": "Please confirm this detail",
                        "html_url": "https://example.invalid/comment/99",
                    }
                ]
            ]
        )
        with patch("todo_flow.triage.command", return_value=pages):
            sources = triage.remote_sources(t)
            self.assertEqual(
                {s["origin"] for s in sources},
                {"github-review", "github-inline", "github-discussion"},
            )
            with self.s.transaction() as c:
                c.execute(
                    "INSERT INTO findings VALUES(?,?,?,?,?)",
                    (sources[0]["id"], "addition", encode(sources[0]), "disposed", time.time()),
                )
            self.assertEqual(len(triage.remote_sources(t)), 2)
