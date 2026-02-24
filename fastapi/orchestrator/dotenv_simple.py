import os
from pathlib import Path


def load_dotenv(dotenv_path: str | None = None) -> None:
    """Very small .env loader (no external dependency).

    - Ignores comments and blank lines.
    - Does not override existing environment variables.
    """
    path = dotenv_path or os.getenv("DOTENV_PATH") or ".env"
    p = Path(path)
    if not p.exists() or not p.is_file():
        return

    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
