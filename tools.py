"""
tools.py — Herramientas locales de escritorio para Ardo Desktop.
Búsqueda web, apertura de URLs, lanzamiento de apps y control HA Bridge.
"""

import os
import re
import subprocess
import urllib.parse
import webbrowser
from typing import Dict, Optional
import sys

try:
    import psutil
except ImportError:
    psutil = None

try:
    from ha_bridge import turn_on_light as _ha_on, turn_off_light as _ha_off
    _HA_AVAILABLE = True
except ImportError:
    _HA_AVAILABLE = False
    def _ha_on(e): return f"HA Bridge no disponible para {e}"
    def _ha_off(e): return f"HA Bridge no disponible para {e}"

_ROOM_TO_ENTITY: Dict[str, str] = {
    "sala":        "light.sala",
    "living":      "light.sala",
    "cuarto":      "light.cuarto",
    "dormitorio":  "light.cuarto",
    "habitacion":  "light.cuarto",
    "recamara":    "light.cuarto",
    "garage":      "light.garage",
    "cochera":     "light.garage",
}


class ToolResult:
    def __init__(self, ok: bool, mensaje: str, datos: dict = None):
        self.ok = ok
        self.mensaje = mensaje
        self.datos = datos or {}


class ToolManager:
    def __init__(self):
        self._tools = {
            "buscar_web":    self._cmd_buscar_web,
            "abrir_url":     self._cmd_abrir_url,
            "lanzar_app":    self._cmd_lanzar_app,
            "sistema_info":  self._cmd_sistema_info,
            "encender_luz":  self._cmd_encender_luz,
            "apagar_luz":    self._cmd_apagar_luz,
        }

    def detectar_y_ejecutar(self, texto: str) -> Optional[ToolResult]:
        texto_lower = texto.lower().strip()

        if texto_lower.startswith(("busca ", "buscar ", "investiga ")):
            if "youtube" in texto_lower:
                match = re.search(
                    r"^(busca en youtube|buscar en youtube|busca videos de|busca|buscar)\s+(.+)",
                    texto, flags=re.IGNORECASE
                )
                if match:
                    query = match.group(2).strip()
                    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
                    webbrowser.open(url)
                    return ToolResult(True, f"🎥 Buscando en YouTube: '{query}'")
            else:
                match = re.search(
                    r"^(busca en google|buscar en google|investiga sobre|investiga|buscar|busca)\s+(.+)",
                    texto, flags=re.IGNORECASE
                )
                if match:
                    return self._cmd_buscar_web(match.group(2).strip())

        if texto_lower.startswith(("abre la app ", "lanza el programa ", "lanza ", "abre el programa ")):
            match = re.search(
                r"^(abre la app|lanza el programa|lanza|abre el programa)\s+(.+)",
                texto, flags=re.IGNORECASE
            )
            if match:
                return self._cmd_lanzar_app(match.group(2).strip())

        if texto_lower.startswith(("ve a ", "abre la web ", "abre el sitio ", "abre ")):
            match = re.search(
                r"^(ve a la web de|ve a|abre la web|abre el sitio|abre)\s+(.+)",
                texto, flags=re.IGNORECASE
            )
            if match:
                objetivo_original = match.group(2).strip()
                objetivo_lower    = objetivo_original.lower()
                atajos_web = {
                    "youtube": "https://www.youtube.com",
                    "google":  "https://www.google.com",
                    "github":  "https://github.com",
                    "netflix": "https://www.netflix.com",
                    "spotify": "https://open.spotify.com",
                    "whatsapp":"https://web.whatsapp.com",
                    "twitter": "https://x.com",
                    "x":       "https://x.com",
                }
                if objetivo_lower in atajos_web:
                    return self._cmd_abrir_url(atajos_web[objetivo_lower])
                if "." in objetivo_lower and " " not in objetivo_lower:
                    return self._cmd_abrir_url(objetivo_original)

        if any(k in texto_lower for k in ["info del sistema", "estado del pc", "cuanta ram"]):
            return self._cmd_sistema_info()

        # HA Bridge — control directo de luces inteligentes
        if any(k in texto_lower for k in ["enciende", "prende", "encender", "prender"]):
            if any(k in texto_lower for k in ["luz", "foco", "lampara"]):
                for room, entity_id in _ROOM_TO_ENTITY.items():
                    if room in texto_lower:
                        return self._cmd_encender_luz(entity_id)
                return self._cmd_encender_luz("light.sala")

        if any(k in texto_lower for k in ["apaga", "apagar"]):
            if any(k in texto_lower for k in ["luz", "foco", "lampara"]):
                for room, entity_id in _ROOM_TO_ENTITY.items():
                    if room in texto_lower:
                        return self._cmd_apagar_luz(entity_id)
                return self._cmd_apagar_luz("light.sala")

        return None

    def _cmd_buscar_web(self, query: str) -> ToolResult:
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
        webbrowser.open(url)
        return ToolResult(True, f"🔍 Buscando en Google: '{query}'")

    def _cmd_abrir_url(self, url: str) -> ToolResult:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        webbrowser.open(url)
        try:
            dominio = url.replace("https://","").replace("http://","").replace("www.","").split("/")[0].split(".")[0].capitalize()
        except Exception:
            dominio = "enlace"
        return ToolResult(True, f"🌐 Abriendo {dominio}…")

    def _cmd_lanzar_app(self, nombre: str) -> ToolResult:
        if not nombre:
            return ToolResult(False, "Nombre de app vacío.")
        aliases = {
            "paint":       "mspaint",
            "calculadora": "calc",
            "bloc de notas":"notepad",
            "word":        "winword",
            "excel":       "excel",
            "archivos":    "explorer",
            "explorador":  "explorer",
            "cmd":         "cmd",
            "terminal":    "cmd",
        }
        app_exe = aliases.get(nombre.lower(), nombre)
        try:
            if os.name == "nt":
                os.system(f'start "" "{app_exe}"')
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-a", nombre])
            else:
                subprocess.Popen([nombre])
            return ToolResult(True, f"🚀 Lanzando: {nombre}")
        except Exception as e:
            return ToolResult(False, f"No se pudo abrir {nombre}: {e}")

    def _cmd_sistema_info(self, *args) -> ToolResult:
        if not psutil:
            return ToolResult(False, "psutil no instalado. Ejecuta: pip install psutil")
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory().percent
        return ToolResult(True, f"💻 CPU: {cpu}%  |  RAM: {ram}%")

    def _cmd_encender_luz(self, entity_id: str) -> ToolResult:
        resultado = _ha_on(entity_id)
        ok = resultado.startswith("OK")
        area = entity_id.replace("light.", "").replace("switch.", "").capitalize()
        msg = f"💡 Luz {area} encendida." if ok else f"⚠️ {resultado}"
        return ToolResult(ok, msg, {"entity_id": entity_id, "resultado": resultado})

    def _cmd_apagar_luz(self, entity_id: str) -> ToolResult:
        resultado = _ha_off(entity_id)
        ok = resultado.startswith("OK")
        area = entity_id.replace("light.", "").replace("switch.", "").capitalize()
        msg = f"🌑 Luz {area} apagada." if ok else f"⚠️ {resultado}"
        return ToolResult(ok, msg, {"entity_id": entity_id, "resultado": resultado})

    def listar_disponibles(self) -> str:
        ha_status = "✓ Activo (simulado)" if _HA_AVAILABLE else "✗ ha_bridge.py no encontrado"
        return (
            "🛠  COMANDOS DISPONIBLES EN ARDO\n\n"

            "── DOMÓTICA (NLU local) ─────────────────\n"
            "    enciende / apaga la luz del cuarto / sala / todas\n"
            "    abre / cierra la cerradura\n"
            "    pon a limpiar el robot  /  para la aspiradora\n"
            "    enciende / apaga la televisión\n"
            "    sube / baja las persianas\n"
            "    ayuda / socorro / me caí  (activa alerta)\n\n"

            "── BÚSQUEDA Y WEB ───────────────────────\n"
            "    busca [término]\n"
            "    busca en youtube [término]\n"
            "    abre youtube / google / github / netflix / spotify / whatsapp / twitter\n"
            "    ve a [sitio.com]\n\n"

            "── PROGRAMAS ────────────────────────────\n"
            "    lanza el programa paint / calculadora / notepad / word / excel\n"
            "    abre el programa explorador / cmd\n\n"

            "── SISTEMA ──────────────────────────────\n"
            "    info del sistema  /  estado del pc  /  cuánta ram\n\n"

            "── MEMORIA PERSONAL ─────────────────────\n"
            "    recuerda que [dato]\n"
            "    mi nombre es [nombre]\n"
            "    prefiero [algo]  /  me gusta [algo]\n"
            "    memoria  (ver todos los recuerdos)\n"
            "    olvida [id o fragmento]  /  olvida todo\n\n"

            "── CASA INTELIGENTE (HA Bridge) ─────────\n"
            f"  Estado: {ha_status}\n"
            "    enciende / apaga la luz de la sala / cuarto / garage\n"
            "  (Para conectar a Home Assistant real, configura ha_bridge.py)\n"
        )
