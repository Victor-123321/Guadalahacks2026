"""
tools.py — Herramientas locales de escritorio para Ardo Desktop.
Búsqueda web, apertura de URLs y lanzamiento de apps. Sin IA.
"""

import os
import re
import subprocess
import urllib.parse
import webbrowser
from typing import List, Dict, Tuple, Optional
import sys

try:
    import psutil
except ImportError:
    psutil = None


class ToolResult:
    def __init__(self, ok: bool, mensaje: str, datos: dict = None):
        self.ok = ok
        self.mensaje = mensaje
        self.datos = datos or {}


class ToolManager:
    def __init__(self):
        self._tools = {
            "buscar_web":  self._cmd_buscar_web,
            "abrir_url":   self._cmd_abrir_url,
            "lanzar_app":  self._cmd_lanzar_app,
            "sistema_info": self._cmd_sistema_info,
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

        return None

    def parsear_respuesta_ia(self, respuesta: str) -> Tuple[str, List[Dict]]:
        acciones = []
        respuesta_limpia = respuesta

        match_search = re.search(r'ABRIR_BUSQUEDA:(.+?)(?:\n|$)', respuesta_limpia)
        if match_search:
            query = match_search.group(1).strip()
            respuesta_limpia = respuesta_limpia.replace(match_search.group(0), "").strip()
            acciones.append({"herramienta": "buscar_web", "args": query})

        match_url = re.search(r'ABRIR_URL:(https?://\S+)', respuesta_limpia)
        if match_url:
            url = match_url.group(1).strip()
            respuesta_limpia = respuesta_limpia.replace(match_url.group(0), "").strip()
            acciones.append({"herramienta": "abrir_url", "args": url})

        for linea in respuesta_limpia.split("\n"):
            if linea.strip().startswith("TOOL:"):
                try:
                    comando = linea.replace("TOOL:", "").strip()
                    partes  = comando.split(":", 1)
                    nombre  = partes[0].strip()
                    args    = partes[1].strip() if len(partes) > 1 else ""
                    if nombre in self._tools:
                        acciones.append({"herramienta": nombre, "args": args})
                        respuesta_limpia = respuesta_limpia.replace(linea, "").strip()
                except Exception:
                    pass

        return respuesta_limpia, acciones

    def ejecutar(self, herramienta: str, **kwargs) -> ToolResult:
        if herramienta not in self._tools:
            return ToolResult(False, f"Herramienta '{herramienta}' no disponible.")
        try:
            func = self._tools[herramienta]
            args = kwargs.get("args", "")
            return func(args) if args else func()
        except Exception as e:
            return ToolResult(False, f"Error en {herramienta}: {str(e)}")

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

    def listar_disponibles(self) -> str:
        return (
            "🛠  **Herramientas locales activas:**\n\n"
            "  ✓ 🔍 Buscar en Google o YouTube\n"
            "  ✓ 🌐 Abrir sitios web\n"
            "  ✓ 🚀 Lanzar programas del PC\n"
            "  ✓ 💻 Ver estado del sistema\n\n"
            "Ejemplos: *'busca en youtube lofi'*, *'abre github'*, *'lanza el programa paint'*"
        )
