"""
memoria.py — Sistema de memoria personal persistente para Ardo Desktop.
Guarda y recupera información sobre el usuario entre sesiones.
"""

import json
import uuid
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

MEMORIA_PATH = Path(__file__).parent / "memoria.json"

TIPOS_RECUERDO = {
    "hecho":        "📌",
    "preferencia":  "❤️",
    "recordatorio": "⏰",
    "tarea":        "✅",
    "general":      "🧠",
}

KEYWORDS_GUARDAR = [
    r"recuerda que (.+)",
    r"anota que (.+)",
    r"no olvides que (.+)",
    r"guarda que (.+)",
    r"me llamo (.+)",
    r"mi nombre es (.+)",
    r"prefiero (.+)",
    r"me gusta(.+)",
]

KEYWORDS_RECORDATORIO = [
    "mañana", "el lunes", "el martes", "el miércoles", "el jueves",
    "el viernes", "la próxima semana", "en una hora", "a las", "el día",
]
KEYWORDS_TAREA        = ["tengo que", "debo", "necesito", "pendiente", "hacer"]
KEYWORDS_PREFERENCIA  = ["prefiero", "me gusta", "favorito", "favorita",
                         "no me gusta", "odio", "amo", "encanta"]


class MemoriaManager:
    def __init__(self, path: Path = MEMORIA_PATH):
        self.path = path
        self._data = self._cargar()
        self._mensajes_sesion: int = 0
        self._registrar_inicio_sesion()

    def _cargar(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text("utf-8"))
            except Exception:
                pass
        return self._estructura_vacia()

    def _guardar(self):
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _estructura_vacia(self) -> dict:
        ahora = datetime.now().isoformat()
        return {
            "usuario": {"nombre": None, "preferencias": [], "contexto": ""},
            "recuerdos": [],
            "resumen_sesion_anterior": "",
            "estadisticas": {
                "total_mensajes": 0,
                "primera_sesion": ahora,
                "ultima_sesion": ahora,
            },
        }

    def _registrar_inicio_sesion(self):
        self._data["estadisticas"]["ultima_sesion"] = datetime.now().isoformat()
        self._guardar()

    def obtener_contexto_para_prompt(self) -> str:
        partes = []
        usuario = self._data.get("usuario", {})
        if nombre := usuario.get("nombre"):
            partes.append(f"El usuario se llama {nombre}.")
        recuerdos = self._data.get("recuerdos", [])
        if recuerdos:
            recientes = sorted(recuerdos, key=lambda r: r["fecha"], reverse=True)[:15]
            lineas = []
            for r in recientes:
                emoji = TIPOS_RECUERDO.get(r.get("tipo", "general"), "🧠")
                lineas.append(f"  {emoji} [{r['fecha'][:10]}] {r['contenido']}")
            partes.append("Lo que sé sobre el usuario:\n" + "\n".join(lineas))
        resumen = self._data.get("resumen_sesion_anterior", "")
        if resumen:
            partes.append(f"Resumen de la última sesión: {resumen}")
        if not partes:
            return ""
        return "\n\n--- MEMORIA ---\n" + "\n".join(partes) + "\n--- FIN ---\n"

    def procesar_mensaje_usuario(self, mensaje: str) -> Optional[str]:
        self._mensajes_sesion += 1
        self._data["estadisticas"]["total_mensajes"] = (
            self._data["estadisticas"].get("total_mensajes", 0) + 1
        )
        msg_lower = mensaje.lower().strip()

        if re.match(r"^/?(memoria|recuerdos)$", msg_lower):
            return self._cmd_listar()
        if re.match(r"^/?olvida\s+todo$", msg_lower):
            return self._cmd_olvida_todo()
        m = re.match(r"^/?olvida\s+(.+)$", msg_lower)
        if m:
            return self._cmd_olvida(m.group(1).strip())

        for patron in KEYWORDS_GUARDAR:
            m = re.search(patron, msg_lower)
            if m:
                contenido = m.group(1).strip().rstrip(".")
                if "llamo" in patron or "nombre" in patron:
                    nombre = contenido.strip().split()[0].capitalize()
                    self._data["usuario"]["nombre"] = nombre
                tipo = self._detectar_tipo(contenido)
                self.agregar_recuerdo(contenido, tipo)
                emoji = TIPOS_RECUERDO.get(tipo, "🧠")
                return f"{emoji} Anotado: *{contenido}*"

        return None

    def procesar_respuesta_lune(self, respuesta: str):
        self._guardar()

    def agregar_recuerdo(self, contenido: str, tipo: str = "general",
                         tags: Optional[list] = None) -> str:
        rid = str(uuid.uuid4())[:8]
        self._data["recuerdos"].append({
            "id": rid,
            "fecha": datetime.now().isoformat(),
            "tipo": tipo,
            "contenido": contenido,
            "tags": tags or [],
        })
        self._guardar()
        return rid

    def cerrar_sesion(self, resumen: str = ""):
        if resumen:
            self._data["resumen_sesion_anterior"] = resumen[:500]
        self._data["estadisticas"]["ultima_sesion"] = datetime.now().isoformat()
        self._guardar()

    def get_nombre_usuario(self) -> Optional[str]:
        return self._data.get("usuario", {}).get("nombre")

    def get_todos_recuerdos(self) -> list:
        return self._data.get("recuerdos", [])

    def get_stats(self) -> dict:
        return self._data.get("estadisticas", {})

    def _cmd_listar(self) -> str:
        recuerdos = self._data.get("recuerdos", [])
        if not recuerdos:
            return "🧭 No tengo nada guardado todavía. Dime *'recuerda que...'* para empezar."
        usuario = self._data.get("usuario", {})
        lineas = ["🧠 **Lo que sé sobre ti:**\n"]
        if nombre := usuario.get("nombre"):
            lineas.append(f"🤗 Nombre: {nombre}")
        por_tipo: dict = {}
        for r in sorted(recuerdos, key=lambda x: x["fecha"], reverse=True):
            t = r.get("tipo", "general")
            por_tipo.setdefault(t, []).append(r)
        for tipo, items in por_tipo.items():
            emoji = TIPOS_RECUERDO.get(tipo, "🧠")
            lineas.append(f"\n{emoji} **{tipo.capitalize()}:**")
            for r in items[:10]:
                lineas.append(f"  `{r['id']}` [{r['fecha'][:10]}] {r['contenido']}")
        stats = self._data.get("estadisticas", {})
        lineas.append(f"\n📊 Total de mensajes: {stats.get('total_mensajes', 0)}")
        lineas.append("*Usa /olvida [id] para borrar un recuerdo.*")
        return "\n".join(lineas)

    def _cmd_olvida(self, fragmento: str) -> str:
        recuerdos = self._data.get("recuerdos", [])
        idx = next((i for i, r in enumerate(recuerdos) if r["id"] == fragmento), None)
        if idx is None:
            idx = next(
                (i for i, r in enumerate(recuerdos) if fragmento in r["contenido"].lower()), None
            )
        if idx is None:
            return f"❌ No encontré ningún recuerdo con *'{fragmento}'*."
        borrado = recuerdos.pop(idx)
        self._guardar()
        return f"🗑 Olvidado: *{borrado['contenido']}*"

    def _cmd_olvida_todo(self) -> str:
        n = len(self._data.get("recuerdos", []))
        self._data["recuerdos"] = []
        self._data["usuario"]["nombre"] = None
        self._data["resumen_sesion_anterior"] = ""
        self._guardar()
        return f"🗑 Memoria borrada. Eliminé {n} recuerdos."

    def _detectar_tipo(self, texto: str) -> str:
        texto_l = texto.lower()
        if any(k in texto_l for k in KEYWORDS_RECORDATORIO): return "recordatorio"
        if any(k in texto_l for k in KEYWORDS_TAREA):        return "tarea"
        if any(k in texto_l for k in KEYWORDS_PREFERENCIA):  return "preferencia"
        return "hecho"
