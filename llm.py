"""
llm.py — Cerebro LLM de ARDO usando Ollama (qwen2.5:7b).
Usa herramientas propias: busqueda web, clima, control de luces y memoria vectorial.
"""

import ollama
import requests
import re
from ddgs import DDGS
from memory import ArdoMemory
from ha_bridge import turn_on_light, turn_off_light


def internet_search(query: str) -> str:
    print(f"[LLM] Buscando en internet: {query}")
    try:
        results = DDGS().text(query, max_results=1)
        resultados = str(results)
        return resultados[:1000] + "... [Truncado]" if len(resultados) > 1000 else resultados
    except Exception as e:
        return f"Error en busqueda: {e}"


def get_weather(location: str) -> str:
    print(f"[LLM] Consultando clima para: {location}")
    try:
        url = f"https://wttr.in/{location}?format=%t+%h"
        response = requests.get(url, timeout=10)
        data = response.text.split()
        return f"el clima en {location} es de {data[0]} con {data[1]} de humedad"
    except Exception as e:
        return f"Error obteniendo el clima: {e}"


def clean_text_for_tts(text: str) -> str:
    text = re.sub(r"<.*?>", "", text)
    text = re.sub(r"[\*\#\_\-\>\`]", "", text)
    text = text.replace("\n", " ")
    return text.strip()


class ArdoBrain:
    def __init__(self, memory: ArdoMemory):
        self.memory = memory
        self.model = "qwen2.5:7b"

        self.system_prompt = {
            "role": "system",
            "content": (
                "Identidad: ARDO. Personalidad: Seria, tecnica, eficiente.\n\n"
                "REGLAS CRITICAS:\n"
                "1. Lee siempre tu historial de conversacion antes de responder.\n"
                "2. Prohibido usar Markdown o listas. Se directo.\n"
                "3. Para ejecutar acciones usa ESTRICTAMENTE el formato: <nombre_herramienta(argumento)>\n\n"
                "HERRAMIENTAS:\n"
                "- <internet_search(consulta)> : Buscar informacion actual.\n"
                "- <get_weather(ciudad)> : Obtener el clima.\n"
                "- <save_memory(dato)> : Guardar datos del usuario permanentemente.\n"
                "- <turn_on_light(light.sala|light.cuarto|light.garage)> : Encender una luz.\n"
                "- <turn_off_light(light.sala|light.cuarto|light.garage)> : Apagar una luz.\n\n"
                "EJEMPLO:\n"
                "Usuario: 'Mi auto es azul'\n"
                "ARDO: <save_memory(El auto del usuario es azul)> Dato registrado en mis sistemas."
            ),
        }

        self.ejemplos_base = [
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "Sistemas principales activos. ARDO a su servicio."},
        ]

        self.tools_dict = {
            "internet_search": internet_search,
            "get_weather":     get_weather,
            "turn_on_light":   turn_on_light,
            "turn_off_light":  turn_off_light,
        }

    def chat(self, user_text: str, chat_id: str = "terminal") -> str:
        def save_memory_chat(dato: str) -> str:
            return self.memory.save_to_long_term(dato, chat_id)

        self.tools_dict["save_memory"] = save_memory_chat

        chat_history = self.memory.get_chat_history(chat_id)
        messages = [self.system_prompt] + self.ejemplos_base + chat_history

        past_memories = self.memory.search_past(user_text, chat_id)
        if past_memories:
            messages.append({
                "role": "system",
                "content": f"DATOS EN MEMORIA PARA ESTE USUARIO: {', '.join(past_memories)}",
            })

        messages.append({"role": "user", "content": user_text})

        try:
            response = ollama.chat(model=self.model, messages=messages, options={"num_ctx": 4096})
            texto_respuesta = response.message.content

            comandos_encontrados = re.findall(r"<(\w+)\((.*?)\)>", texto_respuesta)

            if comandos_encontrados:
                messages.append({"role": "assistant", "content": texto_respuesta})
                for nombre, argumento in comandos_encontrados:
                    if nombre in self.tools_dict:
                        resultado = self.tools_dict[nombre](argumento)
                        messages.append({
                            "role": "system",
                            "content": f"Resultado de <{nombre}>: {resultado}",
                        })
                response = ollama.chat(model=self.model, messages=messages, options={"num_ctx": 4096})
                texto_respuesta = response.message.content

            self.memory.update_short_term(chat_id, "user", user_text)
            self.memory.update_short_term(chat_id, "assistant", texto_respuesta)
            return texto_respuesta

        except Exception as e:
            return f"Error en procesamiento neuronal: {e}."
