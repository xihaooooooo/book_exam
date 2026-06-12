import os

_BOOK_TO_EXAM_HOME = os.path.join(os.path.expanduser("~"), ".book-to-exam")

_ENV_OVERRIDES = {
    "BOOKTOEXAM_LLM_PROVIDER": "llm_provider",
    "BOOKTOEXAM_DEEP_THINK_LLM": "deep_think_llm",
    "BOOKTOEXAM_QUICK_THINK_LLM": "quick_think_llm",
    "BOOKTOEXAM_TEMPERATURE": "temperature",
}


def _apply_env_overrides(config: dict) -> dict:
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        ref = config.get(key)
        if isinstance(ref, bool):
            config[key] = raw.strip().lower() in ("true", "1", "yes", "on")
        elif isinstance(ref, int):
            config[key] = int(raw)
        elif isinstance(ref, float):
            config[key] = float(raw)
        else:
            config[key] = raw
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "llm_provider": "deepseek",
    "deep_think_llm": "deepseek-v4-flash",
    "quick_think_llm": "deepseek-v4-flash",
    "temperature": None,
    "results_dir": os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), "output"),
    "data_cache_dir": os.path.join(_BOOK_TO_EXAM_HOME, "cache"),
})
