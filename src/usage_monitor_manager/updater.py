"""Controllo e applicazione aggiornamenti per istanza.

Riscrittura nativa (Python) della logica di usage-monitor-updater:
check dell'ultima release GitHub, download, verifica, sostituzione binario.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from .models import Instance

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"


@dataclass
class UpdateCheckResult:
    instance_id: str
    current_version: str | None
    latest_version: str
    update_available: bool
    download_url: str | None


def check_latest_release(instance: Instance) -> UpdateCheckResult | None:
    """Interroga la latest release del repo configurato per l'istanza."""
    if not instance.update_repo:
        return None

    resp = requests.get(GITHUB_API.format(repo=instance.update_repo), timeout=15)
    resp.raise_for_status()
    data = resp.json()

    latest_version = data.get("tag_name", "")
    # TODO: confrontare con la versione installata (da leggere dal binario/manifest locale)
    # TODO: individuare l'asset giusto tra data["assets"] per piattaforma/tipo istanza

    return UpdateCheckResult(
        instance_id=instance.id,
        current_version=None,
        latest_version=latest_version,
        update_available=True,  # placeholder finché non c'è il confronto versioni
        download_url=None,
    )


def apply_update(instance: Instance, result: UpdateCheckResult) -> None:
    """Scarica il nuovo binario, lo verifica e sostituisce quello esistente."""
    # TODO: download in file temporaneo
    # TODO: verifica (hash/firma se disponibile)
    # TODO: stop istanza (process_control) -> sostituzione file -> restart
    raise NotImplementedError
