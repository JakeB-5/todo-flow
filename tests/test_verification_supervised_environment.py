"""The identity snapshot reaches the supervised verifier without losing cleanup."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from todo_flow import verification, verification_identity
from todo_flow.process_launch import LaunchGate


class SupervisedEnvironmentTests(unittest.TestCase):
    def test_captured_environment_and_cleanup_survive_success_and_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            name = "TODO_FLOW_SUPERVISED_ENVIRONMENT_TEST"
            with patch.dict(os.environ, {name: "captured"}):
                environment = verification_identity.execution_environment()
            source = (
                "import os,sys; "
                f"assert os.environ[{name!r}] == 'captured'; "
                "assert os.environ['GIT_TERMINAL_PROMPT'] == '0'; "
                "print('snapshot-observed', flush=True); sys.exit(int(sys.argv[1]))"
            )
            for code in (0, 7):
                with self.subTest(code=code):
                    identity = dict(
                        directory=str(directory),
                        track="environment",
                        attempt="attempt",
                        execution=f"exit-{code}",
                    )
                    with patch.dict(os.environ, {name: "changed", "GIT_TERMINAL_PROMPT": "1"}):
                        arguments = dict(env=environment, launch_identity=identity)
                        command = [sys.executable, "-c", source, str(code)]
                        if code:
                            with self.assertRaisesRegex(RuntimeError, "snapshot-observed"):
                                verification.run(command, directory, 5, **arguments)
                        else:
                            self.assertEqual(
                                verification.run(command, directory, 5, **arguments),
                                "snapshot-observed",
                            )
                    LaunchGate(**identity).barrier.require_clear()
