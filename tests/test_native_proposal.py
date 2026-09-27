"""Synthetic host-contract tests, not fixtures claiming Orca wire compatibility."""

from dataclasses import replace
import json
import unittest

from todo_flow.native_proposal import NativeProposalBinding, check_binding, decode_native_proposal
from todo_flow.worker import codex_schema


class NativeProposalTests(unittest.TestCase):
    def setUp(self):
        self.binding = NativeProposalBinding(
            attempt="attempt-one",
            task="work-one",
            generation=1,
            head="a" * 40,
            kind="work",
            host="local",
            worktree="repo-one::/workspace",
            dispatch="dispatch-one",
            session="session-one",
            turn="turn-one",
        )
        self.proposal = dict.fromkeys(codex_schema()["properties"])
        self.proposal["summary"] = "제안"

    def decode(self, text, **kwargs):
        return decode_native_proposal(
            text,
            launch=self.binding,
            current=kwargs.pop("current", self.binding),
            **kwargs,
        )

    def test_complete_unicode_proposal_round_trip(self):
        self.proposal["changes"] = [{"path": "example.py", "content": "value = '한글'\n"}]
        result = self.decode(json.dumps(self.proposal, ensure_ascii=False))
        self.assertEqual(result["summary"], "제안")
        self.assertEqual(result["changes"], self.proposal["changes"])
        self.assertNotIn("question", result)

    def test_no_salvaging_truncated_wrapped_or_concatenated_json(self):
        text = json.dumps(self.proposal)
        for malformed in (
            text[:-1],
            "```json\n" + text + "\n```",
            "progress\n" + text,
            text + "\n" + text,
            text + " trailing output",
            '{"summary":"first","summary":"second"}',
            '{"summary":"x","verify":NaN}',
            '{"summary":"x","verify":Infinity}',
            '{"summary":"x","verify":-Infinity}',
        ):
            with self.subTest(text=malformed):
                with self.assertRaises(ValueError):
                    self.decode(malformed)

    def test_nested_duplicate_properties_are_rejected(self):
        text = json.dumps(self.proposal).replace(
            '"changes": null',
            '"changes": [{"path":"one","path":"two","content":"x"}]',
        )
        with self.assertRaisesRegex(ValueError, "Duplicate JSON property"):
            self.decode(text)

    def test_full_schema_checks_types_nested_fields_enums_and_limits(self):
        invalid = (
            {"verify": 1},
            {"publish": "true"},
            {"changes": [{"path": "a", "content": 12}]},
            {"changes": [{"path": "a", "content": "x", "extra": True}]},
            {"next": [{"kind": "work", "purpose": "continue", "extra": True}]},
            {"conditions": [{"id": "one", "verdict": "approved", "evidence": "x"}]},
            {"findings": [{"observation": "missing evidence"}]},
            {"watches": ["invalid"]},
            {"triage_search": []},
            {"triage_search": ["term"] * 31},
            {"unexpected": True},
            {"summary": "\ud800"},
        )
        for update in invalid:
            with self.subTest(update=repr(update)):
                with self.assertRaises(ValueError):
                    self.decode(json.dumps({**self.proposal, **update}))
        with self.assertRaises(ValueError):
            self.decode(json.dumps({"summary": "missing nullable properties"}))

    def test_every_attribution_change_is_rejected(self):
        replacements = {
            "attempt": "attempt-two",
            "task": "work-two",
            "generation": 2,
            "head": "b" * 40,
            "kind": "assess",
            "host": "remote",
            "worktree": "repo-one::/other",
            "dispatch": "dispatch-two",
            "session": "session-two",
            "turn": "turn-two",
        }
        for field, value in replacements.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "binding changed"):
                    self.decode(
                        json.dumps(self.proposal),
                        current=replace(self.binding, **{field: value}),
                    )

    def test_invalid_claim_or_head_cannot_form_a_binding(self):
        for generation in (True, 0, -1, "1"):
            with self.subTest(generation=generation):
                with self.assertRaises(ValueError):
                    replace(self.binding, generation=generation)
        for head in ("HEAD", "main", "a" * 7, "", None):
            with self.subTest(head=head):
                with self.assertRaises(ValueError):
                    replace(self.binding, head=head)
        with self.assertRaises(ValueError):
            check_binding({}, {})

    def test_review_rejects_reused_session_even_with_a_fresh_turn(self):
        review = replace(self.binding, kind="review", turn="review-turn")
        implementers = frozenset({("local", self.binding.session)})
        with self.assertRaisesRegex(ValueError, "independent session"):
            check_binding(review, review, implementers)
        with self.assertRaisesRegex(ValueError, "provenance"):
            check_binding(review, review)
        fresh = replace(review, session="review-session", dispatch="review-dispatch")
        self.proposal["verdict"] = "met"
        result = decode_native_proposal(
            json.dumps(self.proposal),
            launch=fresh,
            current=fresh,
            implementation_sessions=implementers,
        )
        self.assertEqual(result["verdict"], "met")
        self.proposal["changes"] = [{"path": "a", "content": "x"}]
        with self.assertRaisesRegex(ValueError, "Only a work task"):
            decode_native_proposal(
                json.dumps(self.proposal),
                launch=fresh,
                current=fresh,
                implementation_sessions=implementers,
            )

    def test_question_cannot_smuggle_verification_or_publication(self):
        self.proposal["question"] = "decision needed"
        for field in ("verify", "publish"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "decision wait"):
                    self.decode(json.dumps({**self.proposal, field: True}))
