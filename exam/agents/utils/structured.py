"""结构化输出兼容层：用工具调用模拟 structured output

DeepSeek 等模型的思考模式不支持强制 tool_choice，所以不强制，用提示词引导。
"""

import json
import logging

logger = logging.getLogger(__name__)


def invoke_structured(llm, schema_cls, messages):
    """用工具调用方式实现结构化输出。

    不强制 tool_choice（DeepSeek 思考模式不兼容），
    改为在 messages 末尾追加 JSON 格式提示，引导模型输出 JSON。
    """
    from langchain_core.messages import SystemMessage
    from pydantic import BaseModel

    # 获取 schema 的 JSON 描述
    schema_name = schema_cls.__name__
    schema_fields = {}
    for field_name, field_info in schema_cls.model_fields.items():
        schema_fields[field_name] = {
            "type": _field_type_desc(field_info),
            "description": field_info.description or "",
        }

    # 追加 JSON 输出格式提示
    format_hint = (
        f"\n\n请严格按照以下 JSON 格式输出纯 JSON（不要包裹在 markdown 代码块中），"
        f"所有字段名和枚举值必须用英文：\n"
        f"```\n{json.dumps(_make_example(schema_cls), ensure_ascii=False, indent=2)}\n```"
        f"\n注意：question_type 字段只能是 'choice'、'fill_blank' 或 'short_answer'。"
        f"直接输出 JSON 本身，不要任何其他文字。"
    )

    # 把格式提示加到 system message 里
    modified_messages = list(messages)
    for i, msg in enumerate(modified_messages):
        if hasattr(msg, "type") and msg.type == "system":
            modified_messages[i] = msg.__class__(content=msg.content + format_hint)
            break
    else:
        modified_messages.insert(0, SystemMessage(content=format_hint.strip()))

    result = llm.invoke(modified_messages)
    content = result.content if hasattr(result, "content") else str(result)

    # 尝试解析内容
    return _parse_json_content(content, schema_cls)


def _parse_json_content(content: str, schema_cls):
    """从 LLM 输出中提取 JSON"""
    text = content.strip()

    # 尝试直接解析
    try:
        return schema_cls(**json.loads(text))
    except (json.JSONDecodeError, Exception):
        pass

    # 提取 JSON 代码块
    for marker in ["```json", "```"]:
        if marker in text:
            parts = text.split(marker, 1)
            if len(parts) > 1:
                json_str = parts[1].split("```", 1)[0].strip()
                try:
                    return schema_cls(**json.loads(json_str))
                except (json.JSONDecodeError, Exception):
                    pass

    raise ValueError(f"无法解析结构化输出: {text[:300]}")


def _field_type_desc(field_info) -> str:
    annotation = field_info.annotation
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)


def _make_example(schema_cls) -> dict:
    """生成一个示例 JSON"""
    example = {}
    from pydantic import BaseModel
    for field_name, field_info in schema_cls.model_fields.items():
        desc = field_info.description or field_name
        example[field_name] = f"<{desc}>"
    return example

