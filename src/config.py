"""Loads YAML config and .env settings without extra dependencies."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
DB_PATH = DATA_DIR / "tracker.db"

EXCLUDE_STABLECOINS_FROM_RETURNS = True
BACKFILL_UNIVERSE_SIZE = 200
MILESTONES = (20, 50, 100, 200)


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader: sets os.environ for KEY=VALUE lines not already set."""
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_api_key() -> str | None:
    load_dotenv()
    return os.environ.get("CG_API_KEY") or None


def load_categories() -> tuple[dict[str, str], str]:
    """Returns (coin_id -> category map, fallback category name)."""
    path = CONFIG_DIR / "categories.yaml"
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    mapping: dict[str, str] = {}
    for category, coin_ids in (raw.get("categories") or {}).items():
        for coin_id in coin_ids or []:
            if coin_id in mapping:
                raise ValueError(
                    f"coin_id '{coin_id}' appears in multiple categories "
                    f"('{mapping[coin_id]}' and '{category}') in categories.yaml"
                )
            mapping[coin_id] = category

    fallback = raw.get("fallback", "Other")
    return mapping, fallback


def load_extra_seed_coin_ids() -> list[str]:
    path = CONFIG_DIR / "seed_extra_coins.yaml"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return list(raw.get("extra_coin_ids") or [])
