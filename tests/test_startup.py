"""Basic startup check for the Forge AI scaffold."""

import contextlib
import io
import unittest

from app.main import main


class StartupTests(unittest.TestCase):
    def test_main_reports_healthy_startup(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main()

        self.assertEqual(result, 0)
        self.assertIn("health check: OK", output.getvalue())


if __name__ == "__main__":
    unittest.main()
