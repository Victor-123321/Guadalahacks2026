"""
voice_listener.py — Grabación push-to-talk + transcripción Whisper en red.
Flujo: botón presionado → graba → botón soltado → WAV a Whisper → texto.
Servidor STT: http://192.168.12.1:6767/v1/audio/transcriptions
"""

import io
import json
import logging
import threading
import wave
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


STT_URL      = _load_stt_url()
SAMPLE_RATE  = 16_000
CHANNELS     = 1
BLOCK_FRAMES = 1_024   # ~64 ms por bloque


class VoiceListener(QThread):
    """
    Hilo de captura push-to-talk.
    start_recording() → graba mientras esté activo → stop_recording() → envía a Whisper.
    """

    transcription_ready = pyqtSignal(str)   # texto transcripto listo
    state_changed       = pyqtSignal(str)   # "listo" | "grabando" | "procesando"
    error_occurred      = pyqtSignal(str)   # error no fatal
    barge_in            = pyqtSignal()      # inicio de grabación → interrumpir TTS

    def __init__(self, stt_url: str = STT_URL, parent=None):
        super().__init__(parent)
        self._url          = stt_url
        self._active       = True
        self._recording    = False
        self._frames: list = []
        self._lock         = threading.Lock()

    # ── Control externo (llamado desde el hilo Qt principal) ───────────────────
    def start_recording(self):
        with self._lock:
            self._frames    = []
            self._recording = True
        self.barge_in.emit()
        self.state_changed.emit("grabando")
        _log.debug("PTT: inicio de grabación")

    def stop_recording(self):
        with self._lock:
            self._recording = False
            frames = list(self._frames)
            self._frames = []
        self.state_changed.emit("procesando")
        _log.debug("PTT: fin de grabación — %d bloques", len(frames))
        if frames:
            threading.Thread(target=self._transcribe, args=(frames,), daemon=True).start()
        else:
            self.state_changed.emit("listo")

    def stop_listening(self):
        self._active = False

    # ── Hilo principal: mantiene el stream de micrófono abierto ───────────────
    def run(self):
        try:
            import sounddevice as sd
        except ImportError:
            self.error_occurred.emit("sounddevice no instalado: pip install sounddevice")
            return

        self.state_changed.emit("listo")
        _log.info("VoiceListener PTT activo — STT: %s", self._url)

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=BLOCK_FRAMES,
            ) as stream:
                while self._active:
                    block, _ = stream.read(BLOCK_FRAMES)
                    with self._lock:
                        if self._recording:
                            self._frames.append(block.copy())
        except Exception as e:
            _log.error("VoiceListener error: %s", e)
            self.error_occurred.emit(f"Error de micrófono: {e}")

    # ── Envío a Whisper ────────────────────────────────────────────────────────
    def _transcribe(self, frames: list):
        try:
            audio = np.concatenate(frames, axis=0)
            buf   = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
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
            self.error_occurred.emit(f"Sin conexión STT ({self._url})")
        except Exception as e:
            _log.error("STT excepción: %s", e)
            self.error_occurred.emit(f"Error STT: {e}")
        finally:
            self.state_changed.emit("listo")
