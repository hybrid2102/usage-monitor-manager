"""Tray icon: menu con le istanze configurate e le azioni su ciascuna."""

from __future__ import annotations

import pystray
from PIL import Image, ImageDraw

from . import process_control, updater
from .config import ConfigStore
from .models import Instance


def _build_icon_image() -> Image.Image:
    # TODO: sostituire con un'icona vera del progetto
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(60, 120, 220, 255))
    return img


def _instance_submenu(instance: Instance) -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(
            "Avvia",
            lambda: process_control.start(instance),
            enabled=lambda item: not process_control.is_running(instance),
        ),
        pystray.MenuItem(
            "Ferma",
            lambda: process_control.stop(instance),
            enabled=lambda item: process_control.is_running(instance),
        ),
        pystray.MenuItem("Riavvia", lambda: process_control.restart(instance)),
        pystray.MenuItem(
            "Controlla aggiornamenti",
            lambda: updater.check_latest_release(instance),
        ),
    )


def build_menu(instances: list[Instance]) -> pystray.Menu:
    items = [
        pystray.MenuItem(instance.display_name(), _instance_submenu(instance))
        for instance in instances
    ]
    items.append(pystray.Menu.SEPARATOR)
    items.append(pystray.MenuItem("Esci", lambda icon, item: icon.stop()))
    return pystray.Menu(*items)


def run() -> None:
    instances = ConfigStore().load()
    icon = pystray.Icon(
        "usage-monitor-manager",
        _build_icon_image(),
        "Usage Monitor Manager",
        menu=build_menu(instances),
    )
    icon.run()
