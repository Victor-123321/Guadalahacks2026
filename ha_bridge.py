"""
ha_bridge.py — Puente real con Home Assistant en red local.
URL: http://192.168.12.1:8123  |  Token: config.json → home_assistant.token
Mapea intents TinyNLU a llamadas REST de la API de HA.
"""

import json
import logging
import requests
from pathlib import Path

_log = logging.getLogger("ardo.ha_bridge")


# ─── Configuración (cargada de config.json) ────────────────────────────────────
def _load_ha_config() -> dict:
    cfg_path = Path(__file__).parent / "config.json"
    try:
        data = json.loads(cfg_path.read_text("utf-8"))
        return data.get("home_assistant", {})
    except Exception:
        return {}


_HA_CFG    = _load_ha_config()
HA_URL     = _HA_CFG.get("url",     "http://192.168.12.1:8123")
HA_TOKEN   = _HA_CFG.get("token",   "")
HA_ENABLED = _HA_CFG.get("enabled", True)

# ─── Entidades (sobreescribibles desde config.json → home_assistant.entities) ──
_E = _HA_CFG.get("entities", {})
ENTITIES = {
    "light_main":    _E.get("light_main",    "light.sala"),
    "light_bedroom": _E.get("light_bedroom", "light.cuarto"),
    "light_kitchen": _E.get("light_kitchen", "light.cocina"),
    "door_main":     _E.get("door_main",     "cover.puerta_principal"),
    "door_back":     _E.get("door_back",     "cover.puerta_trasera"),
    "curtain_main":  _E.get("curtain_main",  "cover.cortinas"),
    "robot":         _E.get("robot",         "vacuum.robot_aspiradora"),
    "tv":            _E.get("tv",            "switch.television"),
    "emergency":     _E.get("emergency",     "script.alerta_emergencia"),
}
_ALL_LIGHTS = ["light_main", "light_bedroom", "light_kitchen"]

# ─── Mapa NLU intent + target → (domain, service, entity_key) ─────────────────
_CMD_MAP: dict[tuple, tuple] = {
    ("LIGHT_ON",      "MAIN"):    ("light",  "turn_on",        "light_main"),
    ("LIGHT_ON",      "BEDROOM"): ("light",  "turn_on",        "light_bedroom"),
    ("LIGHT_ON",      "KITCHEN"): ("light",  "turn_on",        "light_kitchen"),
    ("LIGHT_ON",      "ALL"):     ("light",  "turn_on",        "all_lights"),
    ("LIGHT_OFF",     "MAIN"):    ("light",  "turn_off",       "light_main"),
    ("LIGHT_OFF",     "BEDROOM"): ("light",  "turn_off",       "light_bedroom"),
    ("LIGHT_OFF",     "KITCHEN"): ("light",  "turn_off",       "light_kitchen"),
    ("LIGHT_OFF",     "ALL"):     ("light",  "turn_off",       "all_lights"),
    ("DOOR_OPEN",     "MAIN"):    ("cover",  "open_cover",     "door_main"),
    ("DOOR_OPEN",     "BACK"):    ("cover",  "open_cover",     "door_back"),
    ("DOOR_CLOSE",    "MAIN"):    ("cover",  "close_cover",    "door_main"),
    ("DOOR_CLOSE",    "BACK"):    ("cover",  "close_cover",    "door_back"),
    ("CURTAIN_OPEN",  "MAIN"):    ("cover",  "open_cover",     "curtain_main"),
    ("CURTAIN_CLOSE", "MAIN"):    ("cover",  "close_cover",    "curtain_main"),
    ("ROBOT_START",   "MAIN"):    ("vacuum", "start",          "robot"),
    ("ROBOT_START",   "KITCHEN"): ("vacuum", "start",          "robot"),
    ("ROBOT_START",   "BEDROOM"): ("vacuum", "start",          "robot"),
    ("ROBOT_STOP",    "MAIN"):    ("vacuum", "return_to_base", "robot"),
    ("TV_ON",         "MAIN"):    ("switch", "turn_on",        "tv"),
    ("TV_OFF",        "MAIN"):    ("switch", "turn_off",       "tv"),
    ("EMERGENCY",     "MAIN"):    ("script", "turn_on",        "emergency"),
}

# ─── Núcleo HTTP ───────────────────────────────────────────────────────────────
def _token_configured() -> bool:
    return bool(HA_TOKEN) and not HA_TOKEN.startswith("TU_TOKEN")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type":  "application/json",
    }


def _post(domain: str, service: str, entity_id) -> bool:
    """Llama POST /api/services/{domain}/{service}. Retorna True si exitoso."""
    if not HA_ENABLED or not _token_configured():
        _log.debug("Simulado (sin token): %s.%s → %s", domain, service, entity_id)
        return True
    url = f"{HA_URL}/api/services/{domain}/{service}"
    try:
        r = requests.post(url, headers=_headers(), json={"entity_id": entity_id}, timeout=5)
        if r.status_code not in (200, 201):
            _log.warning("HA %s: %s", r.status_code, r.text[:120])
            return False
        return True
    except requests.exceptions.ConnectionError:
        _log.warning("Sin conexión a %s", HA_URL)
        return False
    except Exception as e:
        _log.error("Excepción: %s", e)
        return False


# ─── API pública ───────────────────────────────────────────────────────────────
def execute_nlu_command(intent: str, target: str) -> bool:
    """
    Ejecuta en HA el comando correspondiente al intent+target del TinyNLU.
    Retorna True si la llamada fue exitosa (o simulada por falta de token).
    """
    cmd = _CMD_MAP.get((intent, target)) or _CMD_MAP.get((intent, "MAIN"))
    if not cmd:
        return False

    domain, service, entity_key = cmd

    if entity_key == "all_lights":
        results = [_post(domain, service, ENTITIES[k]) for k in _ALL_LIGHTS]
        return any(results)

    entity_id = ENTITIES.get(entity_key, entity_key)
    return _post(domain, service, entity_id)


def is_connected() -> bool:
    """Comprueba si HA responde. Usado para el indicador de estado en la UI."""
    if not HA_ENABLED or not _token_configured():
        return False
    try:
        r = requests.get(f"{HA_URL}/api/", headers=_headers(), timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# ─── Compatibilidad hacia atrás (tools.py usa estas funciones) ─────────────────
def turn_on_light(entity_id: str) -> str:
    ok = _post("light", "turn_on", entity_id)
    return f"OK: {entity_id} encendido." if ok else f"Error al encender {entity_id}."


def turn_off_light(entity_id: str) -> str:
    ok = _post("light", "turn_off", entity_id)
    return f"OK: {entity_id} apagado." if ok else f"Error al apagar {entity_id}."
