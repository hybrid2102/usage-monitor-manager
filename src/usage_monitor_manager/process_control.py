"""Avvio, stop e riavvio manuale delle istanze configurate."""

from __future__ import annotations

import os
import subprocess

import psutil

from .models import Instance

# Mappa in-memory: instance.id -> PID del processo avviato da questo manager.
_running: dict[str, int] = {}


def is_running(instance: Instance) -> bool:
    pid = _running.get(instance.id)
    if pid is None:
        return False
    return psutil.pid_exists(pid)


def start(instance: Instance) -> None:
    if is_running(instance):
        return
    env = {**os.environ, **instance.env}
    proc = subprocess.Popen(
        [instance.exe_path, *instance.args],
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    _running[instance.id] = proc.pid


def stop(instance: Instance) -> None:
    pid = _running.get(instance.id)
    if pid is None or not psutil.pid_exists(pid):
        _running.pop(instance.id, None)
        return
    psutil.Process(pid).terminate()
    _running.pop(instance.id, None)


def restart(instance: Instance) -> None:
    stop(instance)
    start(instance)
