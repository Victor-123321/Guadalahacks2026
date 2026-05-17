"""
ai_manager.py — Gestor de IA para Ardo Desktop.
Router:
  • TinyNLU  → comandos domóticos (LIGHT_ON, DOOR_OPEN, CHAT_*, …)
  • Ollama   → fallback conversacional cuando TinyNLU retorna UNKNOWN
"""

from typing import Callable, Optional

from tiny_nlu_provider import TinyNLUProvider, nlu_process
from ollama_provider import OllamaProvider


class AIManager:
    def __init__(self):
        self.providers: dict = {
            "tiny_nlu": TinyNLUProvider(),
            "ollama":   OllamaProvider(),
        }

    def reload_provider(self, provider_id: str = None):
        pass

    async def chat(
        self,
        message: str,
        system_prompt: str = "",
        provider: Optional[str] = "auto",
        on_token: Callable = None,
    ) -> str:
        nlu_p   = self.providers["tiny_nlu"]
        ollama_p = self.providers["ollama"]

        if provider in ("auto", "tiny_nlu"):
            # Detectar intent sin generar respuesta todavía
            result = nlu_process(message)

            if result["intent"] == "UNKNOWN":
                # Intentar Ollama como fallback
                if ollama_p.is_available():
                    return await ollama_p.chat(message, system_prompt, on_token=on_token)
                # Sin Ollama: TinyNLU devuelve el mensaje de ayuda
            # TinyNLU maneja IoT y CHAT_*
            return await nlu_p.chat(message, system_prompt, on_token=on_token)

        if provider not in self.providers:
            return f"Proveedor '{provider}' no disponible"
        return await self.providers[provider].chat(message, system_prompt, on_token=on_token)

    def clear_history(self, provider: Optional[str] = None):
        targets = [self.providers[provider]] if provider else self.providers.values()
        for p in targets:
            p.clear_history()

    def ollama_available(self) -> bool:
        return self.providers["ollama"].is_available()
