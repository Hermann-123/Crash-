import json
from pathlib import Path
from typing import Any
from app.core import DATA_PATH, logger


def _file(name: str) -> Path:
    return DATA_PATH / name


def load_json(name: str, default: Any):
    path = _file(name)
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.exception(f"Erreur lecture JSON {name}: {e}")
        return default


def save_json(name: str, data: Any):
    path = _file(name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.exception(f"Erreur écriture JSON {name}: {e}")
