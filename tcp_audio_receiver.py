"""
tcp_audio_receiver.py — Receptor TCP de audio en bruto desde el ESP32-S3.

Flujo completo:
  1. ESP32 detecta "Hey Ardo" (wake word)
  2. ESP32 abre conexión TCP a {desktop_ip}:7111
  3. Transmite audio PCM-16 (mono, 16 kHz, little-endian) en bloques
  4. Cierra la conexión al detectar silencio (VAD en el ESP32)
  5. Desktop acumula los bytes, construye WAV en memoria
  6. POST al servidor Whisper STT → transcripción
  7. Emite transcription_ready(text) → pipeline _process_command

Solo acepta una conexión a la vez (ESP32 no habla mientras se procesa).
"""

import io
import json
import logging
import socket
import wave
from pathlib import Path
from typing import Optional

import requests
from PyQt6.QtCore import QThread, pyqtSignal

_log = logging.getLogger("ardo.tcp_audio")

SAMPLE_RATE  = 16_000
CHANNELS     = 1
SAMPLE_WIDTH = 2   # 16-bit PCM


def _load_cfg() -> dict:
    try:
        raw = json.loads((Path(__file__).parent / "config.json").read_text("utf-8"))
        return raw.get("tcp_audio", {})
    except Exception:
        return {}


def _load_stt_url() -> str:
    try:
        raw = json.loads((Path(__file__).parent / "config.json").read_text("utf-8"))
        return raw.get("stt", {}).get("url", "http://192.168.12.1:6767/v1/audio/transcriptions")
    except Exception:
        return "http://192.168.12.1:6767/v1/audio/transcriptions"


class TCPAudioReceiver(QThread):
    """
    Hilo Qt que escucha en un puerto TCP.
    El ESP32 conecta, transmite PCM-16 en bruto y cierra la conexión al terminar.
    El receptor acumula, convierte a WAV y envía a Whisper STT.

    Señales:
      transcription_ready(text)  → _on_voice_transcription en la UI
      recording_started()        → actualizar face + status (grabando)
      recording_stopped()        → actualizar status (procesando)
      error_occurred(msg)        → log de error no fatal
    """

    transcription_ready = pyqtSignal(str)
    recording_started   = pyqtSignal()
    recording_stopped   = pyqtSignal()
    error_occurred      = pyqtSignal(str)

    def __init__(self, host: str = "0.0.0.0", port: int = 7111, parent=None):
        super().__init__(parent)
        cfg           = _load_cfg()
        self._host    = cfg.get("host",    host)
        self._port    = cfg.get("port",    port)
        self._enabled = cfg.get("enabled", True)
        self._stt_url = _load_stt_url()
        self._active  = True
        self._server_sock: Optional[socket.socket] = None

    # ── Control ────────────────────────────────────────────────────────────────
    def stop(self):
        self._active = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass

    # ── Hilo ───────────────────────────────────────────────────────────────────
    def run(self):
        if not self._enabled:
            _log.info("TCPAudioReceiver desactivado")
            return

        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self._host, self._port))
            srv.listen(1)
            srv.settimeout(1.0)
            self._server_sock = srv
            _log.info("TCPAudio escuchando → %s:%d  (PCM-16, mono, 16 kHz)", self._host, self._port)

            while self._active:
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue

                _log.info("ESP32 conectado desde %s — iniciando captura", addr[0])
                self.recording_started.emit()

                pcm = self._receive(conn)
                conn.close()
                self.recording_stopped.emit()

                # Descartar clips menores a 0.5 s (probablemente ruido)
                min_bytes = int(SAMPLE_RATE * SAMPLE_WIDTH * 0.5)
                if len(pcm) >= min_bytes:
                    _log.info("Audio capturado: %d bytes (%.1f s) — enviando a STT",
                              len(pcm), len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH))
                    self._transcribe(pcm)
                else:
                    _log.debug("Clip muy corto (%d bytes), descartado", len(pcm))

            srv.close()

        except OSError as e:
            _log.error("TCPAudio: puerto %d no disponible — %s", self._port, e)
            self.error_occurred.emit(f"TCP Audio: puerto {self._port} ocupado ({e})")
        except Exception as e:
            _log.error("TCPAudio error: %s", e)
            self.error_occurred.emit(f"TCP Audio error: {e}")

    # ── Recepción ──────────────────────────────────────────────────────────────
    def _receive(self, conn: socket.socket) -> bytes:
        """
        Acumula los bytes PCM-16 hasta que el ESP32 cierra la conexión.
        El cierre de la conexión es la señal de fin de utterance.
        """
        buf = bytearray()
        conn.settimeout(6.0)   # 6 s máximo sin datos antes de dar por terminado
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break       # TCP FIN — ESP32 cerró la conexión
                buf.extend(chunk)
        except socket.timeout:
            _log.debug("Timeout de recepción — usando %d bytes acumulados", len(buf))
        return bytes(buf)

    # ── STT ────────────────────────────────────────────────────────────────────
    def _transcribe(self, pcm: bytes):
        """Construye un WAV en memoria y lo envía al servidor Whisper STT."""
        try:
            wav_buf = io.BytesIO()
            with wave.open(wav_buf, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm)
            wav_buf.seek(0)

            r = requests.post(
                self._stt_url,
                files={"file": ("audio.wav", wav_buf, "audio/wav")},
                data={"task": "transcribe", "language": "es"},
                timeout=20,
            )

            if r.status_code == 200:
                text = r.json().get("text", "").strip()
                if text:
                    _log.info("STT ← ESP32: %r", text)
                    self.transcription_ready.emit(text)
                else:
                    _log.debug("Whisper devolvió texto vacío")
            else:
                msg = f"Whisper error {r.status_code}"
                _log.warning("%s: %s", msg, r.text[:80])
                self.error_occurred.emit(msg)

        except requests.exceptions.ConnectionError:
            self.error_occurred.emit(f"Sin conexión STT ({self._stt_url})")
        except Exception as e:
            _log.error("TCPAudio STT error: %s", e)
            self.error_occurred.emit(f"Error STT: {e}")
