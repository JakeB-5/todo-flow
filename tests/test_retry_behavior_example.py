"""Synthetic before/after example for examples/retry-backoff.html#retry-contract.

Condition retry counts the first call toward the total attempt limit. Expected
values below come from that explicit requirement, not either implementation.
These small functions illustrate one bug; they are not a production retry API.
"""

import unittest
from unittest.mock import Mock


class TemporaryFailure(Exception):
    pass


def retry_before(request, attempt_limit):
    """Wrong: treats the total attempt limit as additional retries."""
    for _ in range(attempt_limit + 1):
        try:
            return request()
        except TemporaryFailure:
            pass
    return "exhausted"


def retry_after(request, attempt_limit):
    """Corrected: the first call is included in the total attempt limit."""
    for _ in range(attempt_limit):
        try:
            return request()
        except TemporaryFailure:
            pass
    return "exhausted"


class RetryBehaviorExampleTests(unittest.TestCase):
    def check_attempt_limit(self, retry):
        # Source: condition retry's explicit total-call requirement in the HTML.
        # A tempting success on call 4 must not be consumed with a limit of 3.
        request = Mock(
            side_effect=[
                TemporaryFailure(),
                TemporaryFailure(),
                TemporaryFailure(),
                "ok",
            ]
        )
        result = retry(request, 3)
        self.assertEqual(
            (result, request.call_count),
            ("exhausted", 3),
            "retry: the total attempt limit includes the first call",
        )

    def test_before_is_rejected_by_the_requirement_check(self):
        # The same assertion used below must fail on the concrete wrong version.
        with self.assertRaisesRegex(self.failureException, "retry:"):
            self.check_attempt_limit(retry_before)

    def test_after_passes_the_same_requirement_check(self):
        self.check_attempt_limit(retry_after)

    def test_success_on_the_last_allowed_call(self):
        request = Mock(side_effect=[TemporaryFailure(), TemporaryFailure(), "ok"])
        self.assertEqual(retry_after(request, 3), "ok")
        self.assertEqual(request.call_count, 3)

    def test_permanent_failure_is_not_retried(self):
        failure = ValueError("permanent failure")
        request = Mock(side_effect=failure)
        with self.assertRaises(ValueError) as caught:
            retry_after(request, 3)
        self.assertIs(caught.exception, failure)
        self.assertEqual(request.call_count, 1)
