import json
import tempfile
import unittest
from unittest.mock import patch

from todo_flow.process_barrier import ProcessBarrier, ProcessBarrierError


class ProcessBarrierTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name
        self.barrier = self.reopen()

    def reopen(self, track="track"):
        return ProcessBarrier(self.directory, track)

    def begin(self, execution="run"):
        return self.barrier.begin(
            "attempt", execution, reason="launch", evidence={"argv": ["worker"]}
        )

    def advance(self, state, revision, *, execution="run", attempt="attempt", evidence=None):
        return self.barrier.advance(
            attempt,
            execution,
            state,
            expected_revision=revision,
            reason="supervisor observation",
            evidence=evidence or {"receipt": "process receipt"},
        )

    def proof(self, execution="run", outcome="group-exited"):
        return {
            "identity": {"track": "track", "attempt": "attempt", "execution": execution},
            "outcome": outcome,
            "proof": "owned supervisor checked all group members",
        }

    def test_restart_blocks_at_every_unresolved_stage_and_preserves_history(self):
        self.reopen().require_clear()
        revision = self.begin()
        for state in ("intent", "running", "cleaning", "unknown", "cleaning"):
            if state != "intent":
                revision = self.advance(state, revision)
            with self.assertRaises(ProcessBarrierError):
                self.reopen().require_clear()
            self.assertEqual(self.reopen().history()[-1]["state"], state)
        self.advance("confirmed", revision, evidence=self.proof())
        self.reopen().require_clear()
        self.assertEqual(len(self.reopen().history()), 6)
        self.assertEqual(self.reopen().history()[0]["evidence"], {"argv": ["worker"]})
        self.begin("second")
        with self.assertRaises(ProcessBarrierError):
            self.reopen().require_clear()
        self.reopen("unrelated").require_clear()

    def test_wrong_identity_stale_revision_and_decision_are_not_confirmation(self):
        self.begin()
        self.advance("cleaning", 1)
        for kwargs in (
            {"execution": "other", "evidence": self.proof("other")},
            {"attempt": "other", "evidence": self.proof()},
            {"evidence": self.proof("other")},
            {"evidence": {"answer": "retry", "cleanup_confirmed": True}},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ProcessBarrierError):
                self.advance("confirmed", 2, **kwargs)
        with self.assertRaises(ProcessBarrierError):
            self.advance("confirmed", 1, evidence=self.proof())
        with self.assertRaises(ProcessBarrierError):
            self.begin("other")
        self.assertEqual(len(self.reopen().history()), 2)
        with self.assertRaises(ProcessBarrierError):
            self.reopen().require_clear()

    def test_transition_cannot_skip_cleanup_or_reuse_execution(self):
        self.begin()
        with self.assertRaises(ProcessBarrierError):
            self.advance("confirmed", 1, evidence=self.proof())
        self.advance("running", 1)
        self.advance("cleaning", 2)
        with self.assertRaises(ProcessBarrierError):
            self.advance("confirmed", 3, evidence=self.proof(outcome="not-spawned"))
        self.advance("confirmed", 3, evidence=self.proof())
        with self.assertRaises(ProcessBarrierError):
            self.begin()
        self.reopen().require_clear()

    def test_proven_launch_prevention_can_clear_intent(self):
        self.begin()
        self.advance("cleaning", 1)
        self.advance("confirmed", 2, evidence=self.proof(outcome="not-spawned"))
        self.reopen().require_clear()

    def test_interrupted_append_and_malformed_records_block_reads_and_updates(self):
        self.begin()
        original = self.barrier.path.read_bytes()
        malformed = json.loads(original)
        malformed["track"] = "other"
        for raw in (
            b"",
            original[:-1],
            original + b"{",
            b"{}\n",
            (json.dumps(malformed) + "\n").encode(),
        ):
            with self.subTest(raw=raw):
                self.barrier.path.write_bytes(raw)
                with self.assertRaises(ProcessBarrierError):
                    self.reopen().require_clear()
                with self.assertRaises(ProcessBarrierError):
                    self.begin("new")
                self.assertEqual(self.barrier.path.read_bytes(), raw)

    def test_failed_write_keeps_previous_stage_blocked(self):
        self.begin()
        with patch("todo_flow.process_barrier.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(ProcessBarrierError):
                self.advance("running", 1)
        with self.assertRaises(ProcessBarrierError):
            self.reopen().require_clear()
        self.assertEqual(self.reopen().history()[0]["state"], "intent")


if __name__ == "__main__":
    unittest.main()
