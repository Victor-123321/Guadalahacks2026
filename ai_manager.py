"""
ai_manager.py — Gestor de IA para Ardo Desktop
Backend único: TinyNLU (motor de comandos local del ESP2)
"""

from typing import Callable, Optional
from tiny_nlu_provider import TinyNLUProvider


class AIManager:
    def __init__(self):
        self.providers = {"tiny_nlu": TinyNLUProvider()}

    def reload_provider(self, provider_id: str = None):
        pass  # TinyNLU no requiere configuración externa

    async def chat(
        self,
        message: str,
        system_prompt: str = "",
        provider: Optional[str] = "tiny_nlu",
        on_token: Callable = None,
    ) -> str:
        if provider not in self.providers:
            return f"❌ Proveedor '{provider}' no disponible"
        return await self.providers[provider].chat(message, system_prompt, on_token=on_token)

    def clear_history(self, provider: Optional[str] = None):
        pass  # TinyNLU es stateless
