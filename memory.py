"""
memory.py — Memoria vectorial de largo plazo para ARDO (ChromaDB).
Separado de memoria.py (memoria de usuario/GUI). Este modulo es para el core LLM.
"""

import chromadb
import uuid
import json
import os


class ArdoMemory:
    def __init__(self):
        self.client = chromadb.PersistentClient(path="./ardo_db")
        self.collection = self.client.get_or_create_collection(name="long_term_memory")
        self.vault_file = "short_term_vault.json"
        self.short_term_vault = self._load_vault()

    def _load_vault(self) -> dict:
        if os.path.exists(self.vault_file):
            try:
                with open(self.vault_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_vault(self):
        with open(self.vault_file, "w", encoding="utf-8") as f:
            json.dump(self.short_term_vault, f, ensure_ascii=False, indent=4)

    def search_past(self, query: str, chat_id: str) -> list:
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=2,
                where={"chat_id": chat_id},
            )
            return results["documents"][0] if results["documents"] else []
        except Exception:
            return []

    def save_to_long_term(self, text: str, chat_id: str) -> str:
        try:
            doc_id = str(uuid.uuid4())
            self.collection.add(
                documents=[text],
                metadatas=[{"chat_id": chat_id}],
                ids=[doc_id],
            )
            print(f"[Memory] Recuerdo guardado [{chat_id}] -> {text}")
            return "Dato guardado en memoria a largo plazo."
        except Exception as e:
            return f"Error al guardar memoria: {e}"

    def get_chat_history(self, chat_id: str) -> list:
        return self.short_term_vault.get(chat_id, [])

    def update_short_term(self, chat_id: str, role: str, content: str):
        if chat_id not in self.short_term_vault:
            self.short_term_vault[chat_id] = []
        self.short_term_vault[chat_id].append({"role": role, "content": content})
        if len(self.short_term_vault[chat_id]) > 10:
            self.short_term_vault[chat_id].pop(0)
        self._save_vault()
