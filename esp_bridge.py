"""
esp_bridge.py — Servidor HTTP local para recibir comandos del ESP32-S3.

Flujo:
  ESP32 hace POST /command {"text": "enciende la luz"}
  → Desktop procesa NLU, actualiza UI, ejecuta HA
  → Responde JSON con intent + response al ESP32

Endpoints:
  POST /command   {"text": "..."}              → NLU completo, actualiza UI
  POST /nlu       {"intent": "...", "target": "..."} → ejecuta directo (ESP ya procesó)
  GET  /status    → {device_id: bool, ...} estado actual
  GET  /health    → {"status": "ok"}
"""

import json
import logging
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Callable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

_log = logging.getLogger("ardo.esp_bridge")


def _load_cfg() -> dict:
    try:
        return json.loads(
            (Path(__file__).parent / "config.json").read_text("utf-8")
        ).get("esp_bridge", {})
    except Exception:
        return {}


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.1.1", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server multihilo para no bloquear con solicitudes lentas."""
    daemon_threads = True


class ESPBridgeServer(QThread):
    """
    Hilo Qt que expone un servidor HTTP local.
    El ESP32 conecta como cliente HTTP y envía comandos de texto o NLU.

    Señales:
      command_received(str)       — texto natural → _process_command en UI
      nlu_received(str, str)      — intent, target → ejecutar sin pasar por NLU de nuevo
    """

    command_received = pyqtSignal(str)       # texto → pipeline completo
    nlu_received     = pyqtSignal(str, str)  # intent, target → ejecución directa

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 7111,
        get_status: Optional[Callable] = None,
        parent=None,
    ):
        super().__init__(parent)
        cfg            = _load_cfg()
        self._host     = cfg.get("host",    host)
        self._port     = cfg.get("port",    port)
        self._enabled  = cfg.get("enabled", True)
        self._get_status = get_status
        self._active   = True
        self._server: Optional[_ThreadingHTTPServer] = None

    # ── API pública ────────────────────────────────────────────────────────────
    def stop(self):
        self._active = False
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass

    def address(self) -> str:
        """Devuelve IP:puerto para mostrar en la UI."""
        return f"{get_local_ip()}:{self._port}"

    # ── Hilo ───────────────────────────────────────────────────────────────────
    def run(self):
        if not self._enabled:
            _log.info("ESPBridge desactivado en config.json")
            return

        bridge = self  # referencia para el closure del handler

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                _log.debug("ESP32 ← %s", fmt % args)

            def _send_json(self, data: dict, status: int = 200):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> Optional[dict]:
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    return json.loads(self.rfile.read(n))
                except Exception:
                    return None

            # ── GET ───────────────────────────────────────────────────────────
            def do_GET(self):
                if self.path == "/health":
                    self._send_json({"status": "ok", "service": "ardo-desktop", "version": "2.0"})

                elif self.path == "/status":
                    states = bridge._get_status() if bridge._get_status else {}
                    self._send_json({"devices": states, "count": len(states)})

                else:
                    self._send_json({"error": "endpoint no encontrado"}, 404)

            # ── POST ──────────────────────────────────────────────────────────
            def do_POST(self):
                data = self._read_json()
                if data is None:
                    self._send_json({"error": "JSON inválido o Content-Length faltante"}, 400)
                    return

                # /command — texto natural, pasa por TinyNLU completo en la UI
                if self.path == "/command":
                    text = str(data.get("text", "")).strip()
                    if not text:
                        self._send_json({"error": "campo 'text' requerido"}, 400)
                        return

                    from tiny_nlu_provider import nlu_process
                    result = nlu_process(text)

                    # Emitir señal al hilo Qt (cola automática de PyQt)
                    bridge.command_received.emit(text)
                    _log.info("ESP32 /command: %r → %s (%.0f%%)",
                              text, result["intent"], result["confidence"] * 100)

                    self._send_json({
                        "ok":         True,
                        "intent":     result["intent"],
                        "target":     result["target"],
                        "response":   result["response"],
                        "confidence": round(result["confidence"], 3),
                    })

                # /nlu — ESP32 ya procesó el NLU localmente, manda solo intent+target
                elif self.path == "/nlu":
                    intent = str(data.get("intent", "")).upper().strip()
                    target = str(data.get("target", "MAIN")).upper().strip()
                    if not intent:
                        self._send_json({"error": "campo 'intent' requerido"}, 400)
                        return

                    bridge.nlu_received.emit(intent, target)
                    _log.info("ESP32 /nlu: %s / %s", intent, target)
                    self._send_json({"ok": True, "intent": intent, "target": target})

                else:
                    self._send_json({"error": "endpoint no encontrado"}, 404)

        try:
            server = _ThreadingHTTPServer((self._host, self._port), _Handler)
            server.timeout = 1.0
            self._server = server
            _log.info("ESPBridge activo → http://%s:%d", get_local_ip(), self._port)
            while self._active:
                server.handle_request()
            server.server_close()
        except OSError as e:
            _log.error("ESPBridge: puerto %d no disponible — %s", self._port, e)
        except Exception as e:
            _log.error("ESPBridge: error inesperado — %s", e)
