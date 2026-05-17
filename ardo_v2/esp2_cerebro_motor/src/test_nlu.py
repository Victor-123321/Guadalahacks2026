"""
test_nlu.py — Puerto Python de tiny_nlu.cpp para pruebas en host.
Lógica idéntica al motor C++ del ESP2.
"""

KW_TABLE = [
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
    ("cortina",    "CURTAIN_OPEN", 0.6), ("persiana","CURTAIN_OPEN", 0.6),
    ("sube",       "CURTAIN_OPEN", 0.5),
    ("baja",       "CURTAIN_CLOSE",0.5), ("cierra la cortina","CURTAIN_CLOSE",1.0),
]

TARGET_TABLE = [
    ("cuarto",     "BEDROOM"), ("dormitorio",  "BEDROOM"),
    ("recámara",   "BEDROOM"), ("habitación",  "BEDROOM"),
    ("cocina",     "KITCHEN"), ("kitchen",     "KITCHEN"),
    ("trasera",    "BACK"),    ("trasero",     "BACK"),
    ("back",       "BACK"),    ("todo",        "ALL"),
    ("todas",      "ALL"),     ("todos",       "ALL"),
]

RESP_TABLE = {
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

PRIORITIES  = {"LIGHT_ON":3,"LIGHT_OFF":3,"DOOR_OPEN":2,"DOOR_CLOSE":2,
               "ROBOT_START":3,"ROBOT_STOP":3,"EMERGENCY":1,"TV_ON":3,
               "TV_OFF":3,"CURTAIN_OPEN":3,"CURTAIN_CLOSE":3,"UNKNOWN":3}
ACTIONS     = {"LIGHT_ON":"on","LIGHT_OFF":"off","DOOR_OPEN":"open",
               "DOOR_CLOSE":"close","ROBOT_START":"move","ROBOT_STOP":"stop",
               "EMERGENCY":"alert","TV_ON":"on","TV_OFF":"off",
               "CURTAIN_OPEN":"open","CURTAIN_CLOSE":"close","UNKNOWN":"noop"}
TARGET_IDS  = {"LIGHT_ON":"light_main","LIGHT_OFF":"light_main",
               "DOOR_OPEN":"door_main","DOOR_CLOSE":"door_main",
               "ROBOT_START":"robot_vacuum","ROBOT_STOP":"robot_vacuum",
               "EMERGENCY":"alert_buzzer","TV_ON":"tv_main","TV_OFF":"tv_main",
               "CURTAIN_OPEN":"curtain_main","CURTAIN_CLOSE":"curtain_main","UNKNOWN":"none"}

_cmd_id = 0

def nlu_process(text: str) -> dict:
    global _cmd_id
    low = text.lower()

    # Scoring
    scores = {}
    for kw, intent, weight in KW_TABLE:
        if kw in low:
            scores[intent] = scores.get(intent, 0.0) + weight

    # Boost emergencia
    if scores.get("EMERGENCY", 0) > 0:
        scores["EMERGENCY"] += 2.0

    # Desambiguar TV
    if scores.get("TV_ON", 0) > 0.3:
        if "apaga" in low or "off" in low:
            scores["TV_OFF"] = scores.get("TV_ON", 0) + scores.get("LIGHT_OFF", 0)
            scores["TV_ON"] = 0.0
            scores["LIGHT_OFF"] = 0.0
        elif "enciende" in low or "prende" in low:
            scores["TV_ON"] = scores.get("TV_ON", 0) + 1.0
            scores["LIGHT_ON"] = 0.0

    # Desambiguar cortinas
    if scores.get("CURTAIN_OPEN", 0) > 0 or scores.get("CURTAIN_CLOSE", 0) > 0:
        if "baja" in low or "cierra" in low:
            scores["CURTAIN_CLOSE"] = scores.get("CURTAIN_CLOSE", 0) + 1.0
        if "sube" in low or "abre" in low:
            scores["CURTAIN_OPEN"] = scores.get("CURTAIN_OPEN", 0) + 1.0

    # Mejor intent
    best = "UNKNOWN"
    best_score = 0.2
    for intent, score in scores.items():
        if score > best_score:
            best_score = score
            best = intent

    # Target
    target = "MAIN"
    for kw, tgt in TARGET_TABLE:
        if kw in low:
            target = tgt
            break

    confidence = 0.0 if best == "UNKNOWN" else min(best_score / 3.0, 1.0)

    # Respuesta
    response = RESP_TABLE.get((best, target),
               RESP_TABLE.get((best, "MAIN"), "Entendido"))

    # JSON
    tgt_str = TARGET_IDS[best]
    if best in ("LIGHT_ON", "LIGHT_OFF"):
        tgt_str = {"BEDROOM":"light_bedroom","KITCHEN":"light_kitchen"}.get(target, "light_main")
    elif best in ("DOOR_OPEN", "DOOR_CLOSE"):
        tgt_str = "door_back" if target == "BACK" else "door_main"

    extra = {}
    if best in ("DOOR_OPEN", "DOOR_CLOSE"):
        extra = {"duration_ms": 8000}
    elif best == "EMERGENCY":
        extra = {"repeat": 5, "msg": "EMERGENCIA"}
    elif best == "ROBOT_START":
        dest = {"KITCHEN":"kitchen","BEDROOM":"bedroom"}.get(target, "living_room")
        extra = {"destination": dest}
    elif target == "ALL":
        extra = {"broadcast": True}

    _cmd_id = (_cmd_id + 1) % 65535
    import json
    cmd_json = json.dumps({
        "type": "cmd", "source": "esp2_nlu",
        "priority": PRIORITIES[best], "target": tgt_str,
        "action": ACTIONS[best], "id": _cmd_id,
        "params": extra
    }, ensure_ascii=False)

    return {"intent": best, "target": target,
            "confidence": confidence, "response": response, "json": cmd_json}


def print_result(text: str):
    r = nlu_process(text)
    print(f"\n> {text}")
    print(f"  Intent:     {r['intent']:<20s} (confidence: {r['confidence']:.2f})")
    print(f"  Target:     {r['target']}")
    print(f"  Response:   {r['response']}")
    print(f"  JSON:       {r['json']}")


TESTS = [
    "enciende la luz",
    "apaga la luz del cuarto",
    "enciende todas las luces",
    "abre la puerta principal",
    "cierra la puerta trasera",
    "pon a limpiar el robot",
    "para la aspiradora",
    "enciende la televisión",
    "apaga la tele",
    "sube las cortinas",
    "cierra la cortina",
    "ayuda me caí",
    "emergencia llama una ambulancia",
    "hola buenas tardes",
]

if __name__ == "__main__":
    print("═" * 55)
    print("  TinyNLU — prueba en host (Python)")
    print("═" * 55)

    for t in TESTS:
        print_result(t)

    print("\n" + "═" * 55)
    print("  Modo interactivo (Ctrl+C para salir)")
    print("═" * 55)

    try:
        while True:
            text = input("\n> ").strip()
            if text:
                print_result(text)
    except (KeyboardInterrupt, EOFError):
        print("\nChao.")
