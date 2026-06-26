"""结构化输出兼容层：用工具调用模拟 structured output

DeepSeek 等模型的思考模式不支持强制 tool_choice，所以不强制，用提示词引导。
"""

import json
import logging

logger = logging.getLogger(__name__)


def invoke_structured(llm, schema_cls, messages):
    """用工具调用方式实现结构化输出，解析失败时返回 fallback 不崩全场。

    不强制 tool_choice（DeepSeek 思考模式不兼容），
    改为在 messages 末尾追加 JSON 格式提示，引导模型输出 JSON。
    """
    from langchain_core.messages import SystemMessage

    try:
        # 获取 schema 的 JSON 描述
        example_json = json.dumps(_make_example(schema_cls), ensure_ascii=False, indent=2)
        format_hint = (
            f"\n\n直接输出以下格式的 JSON，"
            f"不要用 ``` 包裹，不要先写任何说明文字：\n{example_json}"
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

        return _parse_json_content(content, schema_cls)
    except Exception as e:
        logger.warning(f"结构化输出解析失败，返回 fallback: {e}")
        return _make_fallback(schema_cls)


def _parse_json_content(content: str, schema_cls):
    """从 LLM 输出中提取 JSON"""
    text = content.strip()

    # 尝试直接解析
    try:
        return schema_cls(**json.loads(text, strict=False))
    except (json.JSONDecodeError, Exception):
        pass

    # 提取 JSON 代码块
    for marker in ["```json", "```"]:
        if marker in text:
            parts = text.split(marker, 1)
            if len(parts) > 1:
                json_str = parts[1].split("```", 1)[0].strip()
                try:
                    return schema_cls(**json.loads(json_str, strict=False))
                except (json.JSONDecodeError, Exception):
                    pass

    raise ValueError(f"无法解析结构化输出: {text[:300]}")


def _field_type_desc(field_info) -> str:
    annotation = field_info.annotation
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)


def _make_fallback(schema_cls):
    """构造 fallback 实例，避免解析失败崩全场"""
    kwargs = {}
    for field_name, field_info in schema_cls.model_fields.items():
        annotation = field_info.annotation
        if hasattr(annotation, "__origin__") and annotation.__origin__ is list:
            kwargs[field_name] = []
        elif field_name == "verdict":
            kwargs[field_name] = "fail"
        elif field_name == "issues":
            kwargs[field_name] = "LLM 输出格式异常，无法解析审核结果"
        elif annotation is str or (hasattr(annotation, "__origin__") and annotation.__origin__ is str):
            kwargs[field_name] = ""
        elif annotation is int:
            kwargs[field_name] = 0
        elif annotation is bool:
            kwargs[field_name] = False
        else:
            kwargs[field_name] = None
    return schema_cls(**kwargs)


def _make_example(schema_cls) -> dict:
    """生成一个示例 JSON，原生类型用类型匹配的示例值。"""
    example = {}
    from pydantic import BaseModel
    import typing

    for field_name, field_info in schema_cls.model_fields.items():
        annotation = field_info.annotation
        # 处理 list 类型：展开泛型子类型
        if hasattr(annotation, "__origin__") and annotation.__origin__ is list:
            args = typing.get_args(annotation)
            if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
                example[field_name] = [_make_example(args[0])]
            else:
                example[field_name] = [f"<{field_info.description or field_name}>"]
        # 嵌套 BaseModel
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
            example[field_name] = _make_example(annotation)
        # bool: 用 false 而非字符串占位符，避免误导 LLM
        elif annotation is bool:
            example[field_name] = False
        # int
        elif annotation is int:
            example[field_name] = 0
        # float
        elif annotation is float:
            example[field_name] = 0.0
        else:
            example[field_name] = f"<{field_info.description or field_name}>"
    return example

