"""终审排版师：去重、平衡、排序、排版"""


def create_final_editor(config: dict = None):

    def final_editor_node(state):
        # 暂不实现，直接返回所有题目
        return {"final_exam": "试卷生成完成"}

    return final_editor_node
