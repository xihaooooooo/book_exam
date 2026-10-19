import unittest

from book_exam.chief_editor import ChiefEditorRequest, ChiefEditorRequestError


class ChiefEditorRequestTests(unittest.TestCase):
    def test_defaults_and_normalizes_focuses(self) -> None:
        request = ChiefEditorRequest(focuses=[" 重点一 ", "重点二", "重点一"])

        self.assertEqual(request.focuses, ("重点一", "重点二", "重点一"))
        self.assertIsNone(request.target_count)

    def test_accepts_target_count(self) -> None:
        request = ChiefEditorRequest(
            focuses=["重点一", "重点二"],
            target_count=3,
        )

        self.assertEqual(request.target_count, 3)

    def test_rejects_missing_focuses(self) -> None:
        with self.assertRaisesRegex(
            ChiefEditorRequestError, "at least one"
        ):
            ChiefEditorRequest(focuses=[])

    def test_rejects_blank_focus(self) -> None:
        with self.assertRaisesRegex(ChiefEditorRequestError, "blank"):
            ChiefEditorRequest(focuses=["重点一", "  "])

    def test_rejects_target_count_below_focus_count(self) -> None:
        with self.assertRaisesRegex(ChiefEditorRequestError, "at least"):
            ChiefEditorRequest(focuses=["重点一", "重点二"], target_count=1)


if __name__ == "__main__":
    unittest.main()
