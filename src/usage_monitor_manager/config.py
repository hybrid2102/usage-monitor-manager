"""Lettura/scrittura della configurazione delle istanze.

Riscrittura nativa di usage-monitor-config-editor: qui il manager definisce
lo standard delle istanze (invece di dedurlo da shortcut/.lnk sparsi).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import Instance, InstanceType

DEFAULT_CONFIG_PATH = Path.home() / ".usage-monitor-manager" / "config.json"


class ConfigStore:
    """Carica e salva la lista di Instance da/verso un file JSON."""

    def __init__(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.path = path

    def load(self) -> list[Instance]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        instances = []
        for item in raw.get("instances", []):
            item = dict(item)
            item["type"] = InstanceType(item["type"])
            instances.append(Instance(**item))
        return instances

    def save(self, instances: list[Instance]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = {
            "instances": [
                {**asdict(inst), "type": inst.type.value} for inst in instances
            ]
        }
        self.path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
