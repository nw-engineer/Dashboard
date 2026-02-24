import os
from pathlib import Path
from typing import Any, Dict, Optional, Union


def load_dotenv(dotenv_path: Optional[Union[str, Path]] = None, *, override: bool = False) -> None:
    """
    Minimal .env loader (no external deps).
    - Supports KEY=VALUE lines
    - Ignores blank lines and lines starting with '#'
    - Does not expand quotes/escapes; keeps the raw right-hand side (stripped)
    """
    path = Path(dotenv_path or os.getenv("DOTENV_PATH", ".env"))
    if not path.exists() or not path.is_file():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if (k in os.environ) and (not override):
            continue
        os.environ[k] = v


def _resolve_env_placeholders(value: Any) -> Any:
    """
    Resolve:
      - {"env": "VAR_NAME", "default": "..."} -> os.getenv(VAR_NAME, default)
      - "${VAR_NAME}" inside strings (simple replace; no nesting)
    """
    if isinstance(value, dict) and "env" in value and isinstance(value["env"], str):
        default = value.get("default")
        return os.getenv(value["env"], default)

    if isinstance(value, str):
        # Replace occurrences of ${VAR}
        def repl(m):
            var = m.group(1)
            return os.getenv(var, "")
        return __import__("re").sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, value)

    if isinstance(value, list):
        return [_resolve_env_placeholders(x) for x in value]

    if isinstance(value, dict):
        return {k: _resolve_env_placeholders(v) for k, v in value.items()}

    return value


def load_project_config(config_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """
    Load project config JSON. Priority:
      1) argument `config_path`
      2) env ORCH_CONFIG
    If not found, returns {}.

    After loading, resolves env placeholders (see _resolve_env_placeholders).
    """
    path = Path(config_path or os.getenv("ORCH_CONFIG", "")).expanduser()
    if not str(path) or not path.exists() or not path.is_file():
        return {}

    import json
    conf = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(conf, dict):
        raise RuntimeError("Project config must be a JSON object at top-level")
    return _resolve_env_placeholders(conf)
