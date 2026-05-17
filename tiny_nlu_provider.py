"""
tiny_nlu_provider.py — Puerto Python del motor TinyNLU del ESP2 (Ardo v2)
Implementa la interfaz AIProvider para usarse como backend de la GUI Lune.
La lógica es idéntica a tiny_nlu.cpp para garantizar paridad de comportamiento.
"""

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Callable, Optional

# ─── Interfaz base ────────────────────────────────────────────────────────────
class AIProvider(ABC):
    def __init__(self):
        self.cancel_flag = False

    @abstractmethod
    async def chat(self, message: str, system_prompt: str = "", on_token: Callable = None) -> str: pass
    @abstractmethod
    def is_available(self) -> bool: pass
    @abstractmethod
    def clear_history(self) -> None: pass

# ─── Tablas de keywords (idénticas a tiny_nlu.cpp) ───────────────────────────
_KW_TABLE = [
    # Luz ON
    ("enciende",   "LIGHT_ON",  1.0), ("encender",  "LIGHT_ON",  0.9),
    ("prende",     "LIGHT_ON",  1.0), ("prender",   "LIGHT_ON",  0.9),
    ("ilumina",    "LIGHT_ON",  0.8), ("luz on",    "LIGHT_ON",  1.0),
    ("light on",   "LIGHT_ON",  1.0),
    # Luz OFF
    ("apaga",      "LIGHT_OFF", 1.0), ("apagar",    "LIGHT_OFF", 0.9),
    ("apaga la",   "LIGHT_OFF", 0.9), ("luz off",   "LIGHT_OFF", 1.0),
    ("oscuro",     "LIGHT_OFF", 0.6),
    # Puerta OPEN
    ("abre",       "DOOR_OPEN", 1.0), ("abrir",     "DOOR_OPEN", 0.9),
    ("abrela",     "DOOR_OPEN", 0.9), ("open",      "DOOR_OPEN", 0.8),
    ("destrab",    "DOOR_OPEN", 0.8), ("desbloquea","DOOR_OPEN", 0.7),
    # Puerta CLOSE
    ("cierra",     "DOOR_CLOSE",1.0), ("cerrar",    "DOOR_CLOSE",0.9),
    ("ciérrala",   "DOOR_CLOSE",0.9), ("traba",     "DOOR_CLOSE",0.7),
    ("bloquea",    "DOOR_CLOSE",0.7), ("close",     "DOOR_CLOSE",0.8),
    # Robot START
    ("mueve",      "ROBOT_START",1.0),("pon",       "ROBOT_START",0.7),
    ("robot",      "ROBOT_START",0.6),("aspiradora","ROBOT_START",0.8),
    ("limpia",     "ROBOT_START",0.9),("vacuum",    "ROBOT_START",0.8),
    # Robot STOP
    ("para el robot",      "ROBOT_STOP", 1.0),
    ("detén el robot",     "ROBOT_STOP", 1.0),
    ("para la aspiradora", "ROBOT_STOP", 1.0),
    ("stop robot",         "ROBOT_STOP", 1.0),
    # Emergencia
    ("ayuda",      "EMERGENCY", 1.0), ("auxilio",   "EMERGENCY", 1.0),
    ("socorro",    "EMERGENCY", 1.0), ("sos",       "EMERGENCY", 1.0),
    ("emergencia", "EMERGENCY", 1.0), ("caí",       "EMERGENCY", 0.9),
    ("me caí",     "EMERGENCY", 1.0), ("dolor",     "EMERGENCY", 0.8),
    ("accidente",  "EMERGENCY", 0.9), ("ambulancia","EMERGENCY", 0.9),
    ("llama",      "EMERGENCY", 0.5),
    # TV
    ("televisión", "TV_ON",     0.7), ("tele",      "TV_ON",     0.7),
    ("tv",         "TV_ON",     0.7),
    # Cortinas
    ("cortina",    "CURTAIN_OPEN",  0.6), ("persiana", "CURTAIN_OPEN", 0.6),
    ("sube",       "CURTAIN_OPEN",  0.5),
    ("baja",       "CURTAIN_CLOSE", 0.5),
    ("cierra la cortina", "CURTAIN_CLOSE", 1.0),
]

_TARGET_TABLE = [
    ("cuarto",     "BEDROOM"), ("dormitorio",  "BEDROOM"),
    ("recámara",   "BEDROOM"), ("habitación",  "BEDROOM"),
    ("cocina",     "KITCHEN"), ("kitchen",     "KITCHEN"),
    ("trasera",    "BACK"),    ("trasero",     "BACK"),
    ("back",       "BACK"),    ("todo",        "ALL"),
    ("todas",      "ALL"),     ("todos",       "ALL"),
]

_RESP_TABLE = {
    ("LIGHT_ON",    "MAIN"):    "Luz principal encendida",
    ("LIGHT_ON",    "BEDROOM"): "Luz del cuarto encendida",
    ("LIGHT_ON",    "KITCHEN"): "Luz de la cocina encendida",
    ("LIGHT_ON",    "ALL"):     "Todas las luces encendidas",
    ("LIGHT_OFF",   "MAIN"):    "Luz principal apagada",
    ("LIGHT_OFF",   "BEDROOM"): "Luz del cuarto apagada",
    ("LIGHT_OFF",   "ALL"):     "Todas las luces apagadas",
    ("DOOR_OPEN",   "MAIN"):    "Abriendo la puerta principal",
    ("DOOR_OPEN",   "BACK"):    "Abriendo la puerta trasera",
    ("DOOR_CLOSE",  "MAIN"):    "Cerrando la puerta principal",
    ("DOOR_CLOSE",  "BACK"):    "Cerrando la puerta trasera",
    ("ROBOT_START", "MAIN"):    "Robot en marcha",
    ("ROBOT_START", "KITCHEN"): "Robot dirigiéndose a la cocina",
    ("ROBOT_START", "BEDROOM"): "Robot dirigiéndose al cuarto",
    ("ROBOT_STOP",  "MAIN"):    "Robot detenido",
    ("EMERGENCY",   "MAIN"):    "Activando alerta de emergencia. Llamando ayuda.",
    ("TV_ON",       "MAIN"):    "Encendiendo televisión",
    ("TV_OFF",      "MAIN"):    "Apagando televisión",
    ("CURTAIN_OPEN","MAIN"):    "Abriendo cortinas",
    ("CURTAIN_CLOSE","MAIN"):   "Cerrando cortinas",
    ("UNKNOWN",     "MAIN"):    "No entendí el comando",
}

_PRIORITIES = {
    "LIGHT_ON":3,"LIGHT_OFF":3,"DOOR_OPEN":2,"DOOR_CLOSE":2,
    "ROBOT_START":3,"ROBOT_STOP":3,"EMERGENCY":1,"TV_ON":3,
    "TV_OFF":3,"CURTAIN_OPEN":3,"CURTAIN_CLOSE":3,"UNKNOWN":3
}
_ACTIONS = {
    "LIGHT_ON":"on","LIGHT_OFF":"off","DOOR_OPEN":"open",
    "DOOR_CLOSE":"close","ROBOT_START":"move","ROBOT_STOP":"stop",
    "EMERGENCY":"alert","TV_ON":"on","TV_OFF":"off",
    "CURTAIN_OPEN":"open","CURTAIN_CLOSE":"close","UNKNOWN":"noop"
}
_TARGET_IDS = {
    "LIGHT_ON":"light_main","LIGHT_OFF":"light_main",
    "DOOR_OPEN":"door_main","DOOR_CLOSE":"door_main",
    "ROBOT_START":"robot_vacuum","ROBOT_STOP":"robot_vacuum",
    "EMERGENCY":"alert_buzzer","TV_ON":"tv_main","TV_OFF":"tv_main",
    "CURTAIN_OPEN":"curtain_main","CURTAIN_CLOSE":"curtain_main","UNKNOWN":"none"
}

_INTENT_ICONS = {
    "LIGHT_ON":"💡","LIGHT_OFF":"🌑","DOOR_OPEN":"🔓","DOOR_CLOSE":"🔒",
    "ROBOT_START":"🤖","ROBOT_STOP":"⏹","EMERGENCY":"🚨","TV_ON":"📺",
    "TV_OFF":"📴","CURTAIN_OPEN":"🪟","CURTAIN_CLOSE":"🪟","UNKNOWN":"❓"
}

_HELP_MSG = (
    "No entendí ese comando. Puedo ayudarte con:\n\n"
    "💡  Luces: enciende/apaga la luz [del cuarto / cocina / todas]\n"
    "🔓  Puertas: abre/cierra la puerta [trasera]\n"
    "🤖  Robot: pon a limpiar el robot / para la aspiradora\n"
    "📺  TV: enciende/apaga la televisión\n"
    "🪟  Cortinas: sube/baja las cortinas\n"
    "🚨  Emergencia: ayuda / socorro / me caí"
)

_cmd_id = 0

def nlu_process(text: str) -> dict:
    global _cmd_id
    low = text.lower()
    scores: dict[str, float] = {}

    for kw, intent, weight in _KW_TABLE:
        if kw in low:
            scores[intent] = scores.get(intent, 0.0) + weight

    if scores.get("EMERGENCY", 0) > 0:
        scores["EMERGENCY"] += 2.0

    if scores.get("TV_ON", 0) > 0.3:
        if "apaga" in low or "off" in low:
            scores["TV_OFF"] = scores.get("TV_ON", 0) + scores.get("LIGHT_OFF", 0)
            scores["TV_ON"] = 0.0
            scores["LIGHT_OFF"] = 0.0
        elif "enciende" in low or "prende" in low:
            scores["TV_ON"] = scores.get("TV_ON", 0) + 1.0
            scores["LIGHT_ON"] = 0.0

    if scores.get("CURTAIN_OPEN", 0) > 0 or scores.get("CURTAIN_CLOSE", 0) > 0:
        if "baja" in low or "cierra" in low:
            scores["CURTAIN_CLOSE"] = scores.get("CURTAIN_CLOSE", 0) + 1.0
        if "sube" in low or "abre" in low:
            scores["CURTAIN_OPEN"] = scores.get("CURTAIN_OPEN", 0) + 1.0

    # El objeto mencionado debe mandar sobre verbos ambiguos como abre/cierra.
    has_light = any(k in low for k in ("luz", "luces", "foco", "focos", "lampara", "lamparas", "lámpara", "lámparas"))
    has_curtain = any(k in low for k in ("cortina", "cortinas", "persiana", "persianas"))
    has_door = any(k in low for k in ("puerta", "puertas", "cerradura", "cerraduras"))

    if has_light:
        scores["DOOR_OPEN"] = 0.0
        scores["DOOR_CLOSE"] = 0.0
        scores["CURTAIN_OPEN"] = 0.0
        scores["CURTAIN_CLOSE"] = 0.0
        if any(k in low for k in ("enciende", "encender", "prende", "prender", "ilumina")):
            scores["LIGHT_ON"] = max(scores.get("LIGHT_ON", 0.0), 2.5)
        if any(k in low for k in ("apaga", "apagar", "oscuro")):
            scores["LIGHT_OFF"] = max(scores.get("LIGHT_OFF", 0.0), 2.5)

    if has_curtain:
        scores["DOOR_OPEN"] = 0.0
        scores["DOOR_CLOSE"] = 0.0
        scores["LIGHT_ON"] = 0.0
        scores["LIGHT_OFF"] = 0.0
        if any(k in low for k in ("sube", "abre", "abrir")):
            scores["CURTAIN_OPEN"] = max(scores.get("CURTAIN_OPEN", 0.0), 2.5)
        if any(k in low for k in ("baja", "cierra", "cerrar")):
            scores["CURTAIN_CLOSE"] = max(scores.get("CURTAIN_CLOSE", 0.0), 2.5)

    if has_door:
        scores["CURTAIN_OPEN"] = 0.0
        scores["CURTAIN_CLOSE"] = 0.0
        scores["LIGHT_ON"] = 0.0
        scores["LIGHT_OFF"] = 0.0
        if any(k in low for k in ("abre", "abrir", "abrela", "destrab", "desbloquea")):
            scores["DOOR_OPEN"] = max(scores.get("DOOR_OPEN", 0.0), 2.5)
        if any(k in low for k in ("cierra", "cerrar", "ciérrala", "traba", "bloquea")):
            scores["DOOR_CLOSE"] = max(scores.get("DOOR_CLOSE", 0.0), 2.5)

    best, best_score = "UNKNOWN", 0.2
    for intent, score in scores.items():
        if score > best_score:
            best_score, best = score, intent

    target = "MAIN"
    for kw, tgt in _TARGET_TABLE:
        if kw in low:
            target = tgt
            break

    confidence = 0.0 if best == "UNKNOWN" else min(best_score / 3.0, 1.0)
    response = _RESP_TABLE.get((best, target), _RESP_TABLE.get((best, "MAIN"), "Entendido"))

    tgt_str = _TARGET_IDS[best]
    if best in ("LIGHT_ON", "LIGHT_OFF"):
        tgt_str = {"BEDROOM":"light_bedroom","KITCHEN":"light_kitchen"}.get(target, "light_main")
    elif best in ("DOOR_OPEN", "DOOR_CLOSE"):
        tgt_str = "door_back" if target == "BACK" else "door_main"

    extra: dict = {}
    if best in ("DOOR_OPEN", "DOOR_CLOSE"):
        extra = {"duration_ms": 8000}
    elif best == "EMERGENCY":
        extra = {"repeat": 5, "msg": "EMERGENCIA"}
    elif best == "ROBOT_START":
        extra = {"destination": {"KITCHEN":"kitchen","BEDROOM":"bedroom"}.get(target,"living_room")}
    elif target == "ALL":
        extra = {"broadcast": True}

    _cmd_id = (_cmd_id + 1) % 65535
    cmd_json = json.dumps({
        "type":"cmd","source":"esp2_nlu",
        "priority":_PRIORITIES[best],"target":tgt_str,
        "action":_ACTIONS[best],"id":_cmd_id,"params":extra
    }, ensure_ascii=False)

    return {
        "intent": best, "target": target,
        "confidence": confidence, "response": response, "json": cmd_json,
    }

# ─── Proveedor ────────────────────────────────────────────────────────────────
class TinyNLUProvider(AIProvider):
    """Motor de comandos domóticos basado en TinyNLU del ESP2 (Ardo v2)."""

    async def chat(self, message: str, system_prompt: str = "", on_token: Callable = None) -> str:
        if self.cancel_flag:
            return ""
        result = nlu_process(message)
        response = self._format(result)
        if on_token:
            on_token(response)
        return response

    def _format(self, result: dict) -> str:
        if result["intent"] == "UNKNOWN":
            return _HELP_MSG

        icon  = _INTENT_ICONS.get(result["intent"], "✓")
        conf  = result["confidence"]
        bars  = "▓" * int(conf * 10) + "░" * (10 - int(conf * 10))
        lines = [
            f"{icon}  {result['response']}",
            "",
            f"Confianza: {bars} {conf:.0%}",
            f"Comando UART → `{result['json']}`",
        ]
        return "\n".join(lines)

    def is_available(self) -> bool:
        return True

    def clear_history(self) -> None:
        pass  # TinyNLU es stateless
