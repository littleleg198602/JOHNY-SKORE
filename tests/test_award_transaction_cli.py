from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from market_checker_app.scout_runner import main


class AwardTransactionCliTests(unittest.TestCase):
    def test_normal_deferrals_use_retry_exit_code_and_keep_explicit_status(self):
        for status in ("WAIT_QUOTA", "LEASE_LOST", "BUSY", "RATE_LIMITED"):
            with self.subTest(status=status), \
                 patch("sys.argv", ["scout_runner", "--award-subject", "LMT",
                                    "--award-finding-id", "stored-award"]), \
                 patch("market_checker_app.scout_runner.run_award_transactions",
                       return_value={"status": status, "attempted_requests": 0}), \
                 redirect_stdout(StringIO()) as output:
                with self.assertRaises(SystemExit) as stopped:
                    main()
                self.assertEqual(2, stopped.exception.code)
                self.assertEqual(status, json.loads(output.getvalue())["status"])

