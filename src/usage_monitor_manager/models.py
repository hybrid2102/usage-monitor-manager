"""Modelli dati condivisi tra i moduli config/updater/process_control."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InstanceType(str, Enum):
    CLAUDE = "claude"
    COPILOT = "copilot"
    CODEX = "codex"


@dataclass
class Instance:
    """Una singola istanza monitorata (una tray app usage-monitor-*)."""

    id: str
    """Identificatore univoco, es. 'claude-account1'."""

    type: InstanceType

    exe_path: str
    """Percorso dell'eseguibile della tray app."""

    args: list[str] = field(default_factory=list)

    env: dict[str, str] = field(default_factory=dict)
    """Variabili d'ambiente da impostare al lancio (es. CLAUDE_CONFIG_DIR)."""

    update_repo: str | None = None
    """Repo GitHub 'owner/name' da cui controllare/scaricare gli aggiornamenti."""

    label: str | None = None
    """Nome leggibile mostrato nel menu tray (default: id)."""

    def display_name(self) -> str:
        return self.label or self.id
