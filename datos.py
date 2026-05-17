"""
datos.py — Lector de configuración local para Ardo Desktop.
Lee datos.json en la raíz del proyecto. Sin claves de API.
"""

import json
from pathlib import Path

_ROOT = Path(__file__).parent
_PATH = _ROOT / "datos.json"


def _load() -> dict:
    if not _PATH.exists():
        return {}
    return json.loads(_PATH.read_text("utf-8"))


def get_bot() -> dict:
    return _load().get("bot", {})


def get_personajes() -> list:
    return _load().get("personajes", [])


def get_personaje(nombre: str) -> dict:
    personajes = get_personajes()
    nombre_lower = nombre.lower() if nombre else ""
    return next(
        (p for p in personajes if p.get("nombre", "").lower() == nombre_lower),
        personajes[0] if personajes else {},
    )
