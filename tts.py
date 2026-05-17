"""
tts.py — Sintetizador de voz de ARDO usando Kokoro TTS (offline).
Genera audio en CPU sin conexion a internet.
"""

import soundfile as sf
import numpy as np
import os
from kokoro import KPipeline

os.environ["HF_HUB_OFFLINE"] = "1"


class ArdoTTS:
    def __init__(self, voice: str = "em_alex"):
        print("[TTS] Cargando Kokoro TTS en el procesador...")
        self.voice = voice
        self.pipeline = KPipeline(lang_code="e", device="cpu")
        print("[TTS] Sintetizador en linea.")

    def generate_to_file(self, text: str, output_path: str):
        text_clean = text.replace("\n", " ").strip()
        generator = self.pipeline(text_clean, voice=self.voice, speed=1.0)

        audio_chunks = []
        for _graphemes, _phonemes, audio in generator:
            audio_chunks.append(audio)

        if audio_chunks:
            audio_completo = np.concatenate(audio_chunks)
            sf.write(output_path, audio_completo, 24000)
        else:
            print("[TTS] Error: Kokoro no pudo generar audio.")
