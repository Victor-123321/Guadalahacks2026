"""
ha_bridge.py — Puente con Home Assistant para control de hardware real.
En modo simulado usa LUCES_REGISTRADAS. Para HA real, descomenta el bloque requests.
"""

import requests

HA_URL = "http://tu-ip-de-home-assistant:8123/api"
HA_TOKEN = "TU_TOKEN_DE_ACCESO"

LUCES_REGISTRADAS = ["light.sala", "light.cuarto", "light.garage"]


def _verificar_luz(entity_id: str) -> bool:
    # --- CODIGO REAL PARA HOME ASSISTANT (Descomentar cuando se configure) ---
    # headers = {"Authorization": f"Bearer {HA_TOKEN}"}
    # try:
    #     response = requests.get(f"{HA_URL}/states/{entity_id}", headers=headers, timeout=5)
    #     return response.status_code == 200
    # except Exception:
    #     return False

    # --- MODO SIMULADO ---
    return entity_id in LUCES_REGISTRADAS


def turn_on_light(entity_id: str) -> str:
    """Enciende una luz o interruptor inteligente en la casa."""
    if not _verificar_luz(entity_id):
        area = entity_id.replace("light.", "").replace("switch.", "")
        return f"ERROR_NO_EXISTE: No se encontro ninguna luz registrada en '{area}'"

    print(f"[HA Bridge] Encendiendo {entity_id}...")
    # --- CODIGO REAL ---
    # headers = {"Authorization": f"Bearer {HA_TOKEN}"}
    # requests.post(f"{HA_URL}/services/light/turn_on",
    #               json={"entity_id": entity_id}, headers=headers, timeout=5)
    return f"OK: {entity_id} encendido."


def turn_off_light(entity_id: str) -> str:
    """Apaga una luz o interruptor inteligente en la casa."""
    if not _verificar_luz(entity_id):
        area = entity_id.replace("light.", "").replace("switch.", "")
        return f"ERROR_NO_EXISTE: No se encontro ninguna luz registrada en '{area}'"

    print(f"[HA Bridge] Apagando {entity_id}...")
    # --- CODIGO REAL ---
    # headers = {"Authorization": f"Bearer {HA_TOKEN}"}
    # requests.post(f"{HA_URL}/services/light/turn_off",
    #               json={"entity_id": entity_id}, headers=headers, timeout=5)
    return f"OK: {entity_id} apagado."
