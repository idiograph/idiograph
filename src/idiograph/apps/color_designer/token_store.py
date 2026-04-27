# Copyright 2026 Ryan Smith
# SPDX-License-Identifier: Apache-2.0
#
# Idiograph — deterministic semantic graph execution for production AI pipelines.
# https://github.com/idiograph/idiograph

# src/token_store.py
import json
from pathlib import Path


class TokenStore:
    def __init__(self, path: Path):
        self.path = path
        self._flat: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._flat = self._flatten(raw)

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self._unflatten(self._flat), indent=2),
            encoding="utf-8"
        )

    def tokens(self) -> dict[str, str]:
        return dict(self._flat)

    def set(self, key: str, value: str) -> None:
        self._flat[key] = value

    def _flatten(self, obj: dict, prefix: str = "") -> dict[str, str]:
        result = {}
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                result.update(self._flatten(v, full_key))
            else:
                result[full_key] = v
        return result

    def _unflatten(self, flat: dict[str, str]) -> dict:
        result = {}
        for key, value in flat.items():
            parts = key.split(".")
            d = result
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = value
        return result
