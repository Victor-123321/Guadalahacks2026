"""
voice_listener.py — Escucha continua del micrófono con VAD de energía.
Flujo: mic → VAD → WAV en memoria → Whisper en red → texto transcripto.
Servidor STT: http://192.168.12.1:6767/v1/audio/transcriptions
"""

import io
import json
import logging
import wave
from collections import deque
from pathlib import Path

import numpy as np
import requests
from PyQt6.QtCore import QThread, pyqtSignal

_log = logging.getLogger("ardo.voice")

# ─── Configuración ─────────────────────────────────────────────────────────────
def _load_stt_url() -> str:
    cfg = Path(__file__).parent / "config.json"
    try:
        return json.loads(cfg.read_text("utf-8")).get(
            "stt", {}
        ).get("url", "http://192.168.12.1:6767/v1/audio/transcriptions")
    except Exception:
        return "http://192.168.12.1:6767/v1/audio/transcriptions"


STT_URL          = _load_stt_url()
SAMPLE_RATE      = 16_000   # Hz — Whisper espera 16 kHz
CHANNELS         = 1
BLOCK_FRAMES     = 1_024    # ~64 ms por bloque
ENERGY_THRESH    = 400      # RMS mínimo int16 para considerar voz
PRE_ROLL_BLOCKS  = 6        # Bloques guardados antes de detectar voz (~0.4 s)
SILENCE_BLOCKS   = 14       # Bloques de silencio para cerrar segmento (~0.9 s)
MIN_SPEECH_BLOCKS = 8       # Segmento mínimo válido (~0.5 s)


class VoiceListener(QThread):
    """
    Hilo que captura el micrófono continuamente.
    Cuando detecta voz, graba el segmento y lo manda al servidor Whisper.
    """

    transcription_ready = pyqtSignal(str)   # texto transcripto listo
    state_changed       = pyqtSignal(str)   # "escuchando" | "grabando" | "procesando" | "error"
    error_occurred      = pyqtSignal(str)   # mensaje de error no fatal
    barge_in            = pyqtSignal()      # voz detectada → interrumpir TTS

    def __init__(self, stt_url: str = STT_URL, parent=None):
        super().__init__(parent)
        self._url    = stt_url
        self._active = True
        self._muted  = False

    # ── Control externo ────────────────────────────────────────────────────────
    def stop_listening(self):
        self._active = False

    def set_muted(self, muted: bool):
        self._muted = muted

    def toggle_mute(self) -> bool:
        self._muted = not self._muted
        return self._muted

    # ── Hilo principal ─────────────────────────────────────────────────────────
    def run(self):
        try:
            import sounddevice as sd
        except ImportError:
            self.error_occurred.emit("sounddevice no instalado: pip install sounddevice")
            return

        pre_roll     = deque(maxlen=PRE_ROLL_BLOCKS)
        recording    = []
        silence_cnt  = 0
        speech_cnt   = 0
        is_recording = False

        self.state_changed.emit("escuchando")
        _log.info("VoiceListener activo — STT: %s", self._url)

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=BLOCK_FRAMES,
            ) as stream:
                while self._active:
                    block, _ = stream.read(BLOCK_FRAMES)

                    if self._muted:
                        continue

                    rms      = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
                    is_voice = rms > ENERGY_THRESH

                    if not is_recording:
                        pre_roll.append(block.copy())
                        if is_voice:
                            is_recording = True
                            silence_cnt  = 0
                            speech_cnt   = 1
                            recording    = list(pre_roll)
                            self.barge_in.emit()            # interrumpe TTS si está hablando
                            self.state_changed.emit("grabando")
                    else:
                        recording.append(block.copy())
                        if is_voice:
                            speech_cnt += 1
                            silence_cnt = 0
                        else:
                            silence_cnt += 1
                            if silence_cnt >= SILENCE_BLOCKS:
                                if speech_cnt >= MIN_SPEECH_BLOCKS:
                                    self._transcribe(recording)
                                # Reiniciar estado
                                recording    = []
                                is_recording = False
                                silence_cnt  = 0
                                speech_cnt   = 0
                                self.state_changed.emit("escuchando")

        except Exception as e:
            _log.error("VoiceListener error: %s", e)
            self.error_occurred.emit(f"Error de micrófono: {e}")

    # ── Envío a Whisper ────────────────────────────────────────────────────────
    def _transcribe(self, frames: list):
        self.state_changed.emit("procesando")
        try:
            audio = np.concatenate(frames, axis=0)

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)          # int16 = 2 bytes
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio.tobytes())
            buf.seek(0)

            r = requests.post(
                self._url,
                files={"file": ("audio.wav", buf, "audio/wav")},
                data={"task": "transcribe", "language": "es"},
                timeout=20,
            )

            if r.status_code == 200:
                text = r.json().get("text", "").strip()
                if text:
                    _log.info("STT: %r", text)
                    self.transcription_ready.emit(text)
            else:
                _log.warning("STT %s: %s", r.status_code, r.text[:80])
                self.error_occurred.emit(f"Whisper error {r.status_code}")

        except requests.exceptions.ConnectionError:
            self.error_occurred.emit(f"Sin conexión al servidor STT ({self._url})")
        except Exception as e:
            _log.error("STT excepción: %s", e)
            self.error_occurred.emit(f"Error STT: {e}")
        finally:
            self.state_changed.emit("escuchando")
