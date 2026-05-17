"""
ollama_provider.py — Proveedor Ollama (qwen2.5:7b) en red local.
Se conecta a http://192.168.12.1:11434 (config.json → ollama.host).
Actúa como fallback conversacional cuando TinyNLU retorna UNKNOWN.
"""

import asyncio
import json
from pathlib import Path
from typing import Callable

import ollama as _ollama_lib

from tiny_nlu_provider import AIProvider


# ─── Configuración ─────────────────────────────────────────────────────────────
def _load_ollama_cfg() -> dict:
    cfg_path = Path(__file__).parent / "config.json"
    try:
        return json.loads(cfg_path.read_text("utf-8")).get("ollama", {})
    except Exception:
        return {}


_CFG          = _load_ollama_cfg()
OLLAMA_HOST   = _CFG.get("host",    "http://192.168.12.1:11434")
OLLAMA_MODEL  = _CFG.get("model",   "qwen2.5:7b")
OLLAMA_CTX    = _CFG.get("num_ctx", 2048)
OLLAMA_TIMEOUT= _CFG.get("timeout", 30)
OLLAMA_ENABLED= _CFG.get("enabled", True)

_SYSTEM_PROMPT = (
    "Eres ARDO, asistente domótico de voz para personas con discapacidad motriz. "
    "Personalidad: seria, técnica, directa. "
    "Reglas: responde siempre en español, sin Markdown, sin listas, sin emojis. "
    "Respuestas cortas (máximo 2 oraciones). "
    "Si el usuario pide controlar dispositivos (luces, puertas, etc.), "
    "indícale que use comandos de voz directos como 'enciende la luz' o 'abre la puerta'."
)


class OllamaProvider(AIProvider):
    """
    Proveedor de conversación general usando Ollama en la red local.
    Hereda AIProvider (mismo contrato que TinyNLUProvider).
    """

    def __init__(self, host: str = OLLAMA_HOST, model: str = OLLAMA_MODEL):
        super().__init__()
        self._model   = model
        self._host    = host
        self._history: list[dict] = []
        self._client  = _ollama_lib.Client(host=host)

    # ── Comprobación de disponibilidad ─────────────────────────────────────────
    def is_available(self) -> bool:
        if not OLLAMA_ENABLED:
            return False
        try:
            self._client.list()
            return True
        except Exception:
            return False

    def clear_history(self) -> None:
        self._history.clear()

    # ── Chat ───────────────────────────────────────────────────────────────────
    async def chat(
        self,
        message: str,
        system_prompt: str = "",
        on_token: Callable = None,
    ) -> str:
        if self.cancel_flag:
            return ""

        sysprompt = system_prompt or _SYSTEM_PROMPT
        messages = (
            [{"role": "system", "content": sysprompt}]
            + self._history[-10:]
            + [{"role": "user", "content": message}]
        )

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._client.chat(
                    model=self._model,
                    messages=messages,
                    options={"num_ctx": OLLAMA_CTX},
                ),
            )
            text = response.message.content

            self._history.append({"role": "user",      "content": message})
            self._history.append({"role": "assistant",  "content": text})

            if on_token:
                on_token(text)
            return text

        except Exception as e:
            return f"Ollama no disponible en {self._host}: {e}"
