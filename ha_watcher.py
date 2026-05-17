"""
ha_watcher.py — Sincronización en tiempo real con Home Assistant.
Poll cada 2 s → detecta cambios → emite states_updated → actualiza UI.
El último estado conocido se guarda en ha_states_cache.json para arranque offline.
"""

import json
import logging
import time
from pathlib import Path

import requests
from PyQt6.QtCore import QThread, pyqtSignal

_log = logging.getLogger("ardo.ha_watcher")
_CACHE = Path(__file__).parent / "ha_states_cache.json"


def _load_ha_config() -> dict:
    try:
        cfg = json.loads((Path(__file__).parent / "config.json").read_text("utf-8"))
        return cfg.get("home_assistant", {})
    except Exception:
        return {}


def _is_active(entity_id: str, ha_state: str) -> bool:
    """Traduce el estado textual de HA a bool según el dominio del dispositivo."""
    domain = entity_id.split(".")[0]
    if domain in ("light", "switch", "fan", "input_boolean", "media_player"):
        return ha_state == "on"
    if domain == "cover":
        # En nuestra UI: True = cerrado/bloqueado, False = abierto
        return ha_state in ("closed", "closing")
    if domain == "vacuum":
        return ha_state in ("cleaning", "returning")
    if domain == "climate":
        return ha_state != "off"
    return ha_state not in ("off", "unavailable", "unknown")


class HAWatcher(QThread):
    """
    Hilo que consulta /api/states de Home Assistant cada POLL_INTERVAL segundos.
    Emite states_updated sólo cuando detecta un cambio real.
    Al arrancar carga la caché local para que la UI tenga datos inmediatos.
    """

    states_updated = pyqtSignal(dict)   # {device_id: bool} — sólo los mapeados
    connected      = pyqtSignal(bool)   # True si HA responde correctamente

    POLL_INTERVAL = 2   # segundos

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = True
        self._last   = {}

        cfg = _load_ha_config()
        self._url      = cfg.get("url",      "http://192.168.12.1:8123")
        self._token    = cfg.get("token",    "")
        self._enabled  = cfg.get("enabled",  True)
        self._entities = cfg.get("entities", {})   # {device_id: entity_id}
        self._headers  = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }

    def stop(self):
        self._active = False

    # ── Hilo ───────────────────────────────────────────────────────────────────
    def run(self):
        # Cargar caché local inmediatamente (UI no espera a la red)
        cached = self._load_cache()
        if cached:
            self.states_updated.emit(cached)
            self._last = dict(cached)

        while self._active:
            states = self._fetch()
            if states is not None:
                self.connected.emit(True)
                self._save_cache(states)
                if states != self._last:
                    self.states_updated.emit(states)
                    self._last = dict(states)
            else:
                self.connected.emit(False)
            time.sleep(self.POLL_INTERVAL)

    # ── REST ───────────────────────────────────────────────────────────────────
    def _token_ok(self) -> bool:
        return bool(self._token) and not self._token.startswith("TU_TOKEN")

    def _fetch(self) -> dict | None:
        if not self._enabled or not self._token_ok():
            return None
        try:
            r = requests.get(
                f"{self._url}/api/states",
                headers=self._headers,
                timeout=4,
            )
            if r.status_code != 200:
                _log.debug("HA states %s", r.status_code)
                return None

            # Indexar todos los estados por entity_id
            ha_all = {s["entity_id"]: s["state"] for s in r.json()}

            # Filtrar sólo los dispositivos mapeados en config.json
            result = {}
            for dev_id, entity_id in self._entities.items():
                if entity_id in ha_all:
                    result[dev_id] = _is_active(entity_id, ha_all[entity_id])

            return result

        except requests.exceptions.ConnectionError:
            _log.debug("HA sin conexión")
            return None
        except Exception as e:
            _log.debug("HA fetch error: %s", e)
            return None

    # ── Caché local ────────────────────────────────────────────────────────────
    def _load_cache(self) -> dict:
        try:
            return json.loads(_CACHE.read_text("utf-8"))
        except Exception:
            return {}

    def _save_cache(self, states: dict):
        try:
            _CACHE.write_text(json.dumps(states, indent=2), encoding="utf-8")
        except Exception:
            pass
