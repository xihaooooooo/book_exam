import unittest

from book_exam.chief_editor import (
    ChiefEditorPlan,
    ChiefEditorPlanError,
    QuestionTask,
)


class QuestionTaskTests(unittest.TestCase):
    def test_normalizes_pages_and_brief(self) -> None:
        task = QuestionTask(
            focus_index=1,
            source_pages=[12, 13],
            task_brief=" 比较两种工作方式 ",
        )

        self.assertEqual(task.focus_index, 1)
        self.assertEqual(task.source_pages, (12, 13))
        self.assertEqual(task.task_brief, "比较两种工作方式")

    def test_rejects_negative_focus_index(self) -> None:
        with self.assertRaisesRegex(ChiefEditorPlanError, "focus_index"):
            QuestionTask(
                focus_index=-1,
                source_pages=[1],
                task_brief="说明基本定义",
            )

    def test_rejects_missing_source_pages(self) -> None:
        with self.assertRaisesRegex(ChiefEditorPlanError, "at least one"):
            QuestionTask(
                focus_index=0,
                source_pages=[],
                task_brief="说明基本定义",
            )

    def test_rejects_non_positive_source_page(self) -> None:
        with self.assertRaisesRegex(ChiefEditorPlanError, "positive"):
            QuestionTask(
                focus_index=0,
                source_pages=[0],
                task_brief="说明基本定义",
            )

    def test_rejects_blank_task_brief(self) -> None:
        with self.assertRaisesRegex(ChiefEditorPlanError, "blank"):
            QuestionTask(
                focus_index=0,
                source_pages=[1],
                task_brief="  ",
            )


class ChiefEditorPlanTests(unittest.TestCase):
    def test_normalizes_tasks(self) -> None:
        task = QuestionTask(
            focus_index=0,
            source_pages=[8, 9],
            task_brief="分析条件变化带来的影响",
        )

        plan = ChiefEditorPlan(tasks=[task])

        self.assertEqual(plan.tasks, (task,))

    def test_rejects_empty_plan(self) -> None:
        with self.assertRaisesRegex(ChiefEditorPlanError, "at least one"):
            ChiefEditorPlan(tasks=[])


if __name__ == "__main__":
    unittest.main()
