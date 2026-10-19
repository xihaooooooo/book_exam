import unittest

from book_exam.chief_editor import (
    ChiefEditorRequest,
    ChiefEditorQuota,
    calculate_quota,
)


class ChiefEditorQuotaTests(unittest.TestCase):
    def test_defaults_to_two_questions_per_focus(self) -> None:
        request = ChiefEditorRequest(focuses=["重点一", "重点二", "重点三"])

        quota = calculate_quota(request)

        self.assertEqual(
            quota,
            ChiefEditorQuota(
                target_count=6,
                focus_counts=(2, 2, 2),
            ),
        )

    def test_distributes_extra_questions_in_focus_order(self) -> None:
        request = ChiefEditorRequest(
            focuses=["重点一", "重点二", "重点三"],
            target_count=7,
        )

        quota = calculate_quota(request)

        self.assertEqual(quota.focus_counts, (3, 2, 2))


if __name__ == "__main__":
    unittest.main()
