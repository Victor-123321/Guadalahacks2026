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
            peak = np.max(np.abs(audio_completo))
            if peak > 0:
                audio_completo = audio_completo / peak * 0.9
            # Kokoro genera a 24 kHz; remuestreamos a 44.1 kHz para una
            # reproduccion mas consistente en Windows.
            source_rate = 24000
            target_rate = 44100
            if source_rate != target_rate:
                old_x = np.linspace(0, 1, num=len(audio_completo), endpoint=False)
                new_len = int(len(audio_completo) * target_rate / source_rate)
                new_x = np.linspace(0, 1, num=new_len, endpoint=False)
                audio_completo = np.interp(new_x, old_x, audio_completo)
            sf.write(output_path, audio_completo, target_rate)
        else:
            print("[TTS] Error: Kokoro no pudo generar audio.")
