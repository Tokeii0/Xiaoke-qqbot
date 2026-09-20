from __future__ import annotations

import unittest

from xiaoke_bot.join_gate import extract_allowed_qqs

SAMPLE = {
    "exampleProduct": {
        "total": 5,
        "users": [
            {"qq": "1001", "lifetimeLicenseCount": 2, "totalLicenseCount": 3},   # pass
            {"qq": "1002", "lifetimeLicenseCount": 1, "totalLicenseCount": 1},   # fail (both 1)
            {"qq": "1003", "lifetimeLicenseCount": 5, "totalLicenseCount": 1},   # fail (total 1)
            {"qq": "1004", "lifetimeLicenseCount": 1, "totalLicenseCount": 9},   # fail (lifetime 1)
            {"qq": "", "lifetimeLicenseCount": 9, "totalLicenseCount": 9},        # fail (no qq)
            {"qq": "abc", "lifetimeLicenseCount": 9, "totalLicenseCount": 9},     # fail (non-digit)
        ],
    },
    "anotherProduct": {
        "users": [
            {"qq": "2001", "lifetimeLicenseCount": 2, "totalLicenseCount": 2},   # pass
        ]
    },
}


class JoinGateParserTests(unittest.TestCase):
    def test_only_both_counts_at_or_above_threshold_pass(self) -> None:
        self.assertEqual(extract_allowed_qqs(SAMPLE, 2), {1001, 2001})

    def test_threshold_one_lets_single_license_holders_in(self) -> None:
        # with min=1, everyone with a valid qq and >=1 of each qualifies
        self.assertEqual(extract_allowed_qqs(SAMPLE, 1), {1001, 1002, 1003, 1004, 2001})

    def test_higher_threshold_excludes(self) -> None:
        self.assertEqual(extract_allowed_qqs(SAMPLE, 3), set())  # none has both >= 3

    def test_malformed_inputs(self) -> None:
        self.assertEqual(extract_allowed_qqs(None, 2), set())
        self.assertEqual(extract_allowed_qqs({}, 2), set())
        self.assertEqual(extract_allowed_qqs({"p": {"users": "nope"}}, 2), set())
        self.assertEqual(extract_allowed_qqs({"p": {"users": [{"qq": "9"}]}}, 2), set())


if __name__ == "__main__":
    unittest.main()
