"""出题流程通用错误。"""


class GenerationError(ValueError):
    """出题请求、计划或结果不满足流程约束。"""
