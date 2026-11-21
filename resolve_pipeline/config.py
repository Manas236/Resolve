"""Read local configuration with process environment taking precedence."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


def read_env_file(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}

    values = {}
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
            raise ValueError(f"Invalid environment assignment on line {number}")
        if name in values:
            raise ValueError(f"Duplicate environment key on line {number}")
        values[name] = value.strip()
    return values


@dataclass(frozen=True)
class Config:
    state_dir: Path
    log_level: int
    audience: str

    @classmethod
    def load(cls, env_file: Path, environ: Mapping[str, str]) -> "Config":
        values = read_env_file(env_file) | dict(environ)
        state = values.get("RESOLVE_STATE_DIR", "state").strip()
        level = values.get("RESOLVE_LOG_LEVEL", "INFO").strip().upper()
        audience = values.get(
            "RESOLVE_AUDIENCE",
            "English-speaking recreational lifters building consistent strength-training habits",
        ).strip()
        if not state or not audience:
            raise ValueError("RESOLVE_STATE_DIR and RESOLVE_AUDIENCE must be nonempty")
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("Invalid RESOLVE_LOG_LEVEL")
        state_dir = Path(state).expanduser()
        if not state_dir.is_absolute():
            state_dir = env_file.absolute().parent / state_dir
        return cls(state_dir, logging.getLevelName(level), audience)

    def prepare(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("downloads", "media", "cache"):
            (self.state_dir / name).mkdir(exist_ok=True, mode=0o700)
