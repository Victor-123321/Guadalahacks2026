"""
device_bridge.py — Puente HTTP bidireccional para dispositivos IoT.

Cualquier dispositivo de la red (ESP32, Raspberry Pi, sensor, etc.)
puede comunicarse con Ardo Desktop a través de este servidor.

── Entrada (dispositivo → desktop) ──────────────────────────────────────
  POST /command          {"text": "...", "device_id": "..."}
                         Texto natural → pipeline NLU completo en la UI

  POST /nlu              {"intent": "LIGHT_ON", "target": "BEDROOM",
                          "device_id": "..."}
                         Intent ya procesado → ejecución directa (sin doble NLU)

  POST /data             {"device_id": "sensor_temp", "type": "temperature",
                          "value": 23.5, "unit": "°C"}
                         Dato de sensor → señal data_received en UI

  POST /event            {"device_id": "sensor_mov", "event": "motion_detected",
                          "meta": {"location": "sala"}}
                         Evento de dispositivo → puede disparar acciones NLU

── Salida (desktop → dispositivo) ───────────────────────────────────────
  POST /send/{device_id} {"cmd": "turn_on", "pin": 2}
                         Encola un mensaje para el dispositivo

  GET  /inbox/{device_id}
                         Dispositivo recoge sus mensajes pendientes (vacía la cola)

── Consulta ─────────────────────────────────────────────────────────────
  GET  /status           → {device_id: bool} estados de dispositivos desktop
  GET  /devices          → dispositivos IoT registrados + último dato
  GET  /health           → {"status": "ok", "devices_online": N}
"""

import json
import logging
import socket
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Callable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

_log = logging.getLogger("ardo.device_bridge")

# Mapeo de eventos estándar a texto NLU (el desktop los procesa como si el usuario los dijera)
EVENT_TO_COMMAND: dict[str, str] = {
    "emergency_button":  "ayuda emergencia",
    "light_on":          "enciende la luz",
    "light_off":         "apaga la luz",
    "all_lights_on":     "enciende todas las luces",
    "all_lights_off":    "apaga todas las luces",
    "robot_start":       "pon a limpiar el robot",
    "robot_stop":        "para el robot",
    "tv_on":             "enciende la televisión",
    "tv_off":            "apaga la televisión",
    "door_open":         "abre la puerta",
    "door_close":        "cierra la puerta",
    "curtain_open":      "sube las persianas",
    "curtain_close":     "baja las persianas",
}


def _load_cfg() -> dict:
    try:
        raw = json.loads((Path(__file__).parent / "config.json").read_text("utf-8"))
        # Soporta tanto "device_bridge" como "esp_bridge" (compatibilidad)
        return raw.get("device_bridge", raw.get("esp_bridge", {}))
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
    """HTTP multihilo — cada request se atiende en su propio hilo."""
    daemon_threads = True


class DeviceBridgeServer(QThread):
    """
    Servidor HTTP que corre en un QThread.
    Recibe datos, eventos y comandos de cualquier dispositivo IoT de la red local.

    Señales (siempre emitidas al hilo Qt principal via cola automática):
      command_received(text)               → texto → _process_command en UI
      nlu_received(intent, target)         → intent directo → ejecutar en HA
      data_received(device_id, type, val)  → dato de sensor
      event_received(device_id, event, meta_json) → evento con metadatos
    """

    command_received = pyqtSignal(str)           # texto natural
    nlu_received     = pyqtSignal(str, str)      # intent, target
    data_received    = pyqtSignal(str, str, str) # device_id, type, value_str
    event_received   = pyqtSignal(str, str, str) # device_id, event_name, meta_json

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 7111,
        get_status: Optional[Callable] = None,
        parent=None,
    ):
        super().__init__(parent)
        cfg = _load_cfg()
        self._host      = cfg.get("host",    host)
        self._port      = cfg.get("port",    port)
        self._enabled   = cfg.get("enabled", True)
        self._get_status = get_status
        self._active    = True
        self._server: Optional[_ThreadingHTTPServer] = None

        # Registro de dispositivos IoT que han hecho contacto
        self._lock    = threading.Lock()
        self._iot_devices: dict[str, dict] = {}  # {device_id: {last_seen, type, last_value}}
        self._outbox:       dict[str, list] = {}  # {device_id: [msg, ...]} — cola de salida

    # ── API pública ────────────────────────────────────────────────────────────
    def stop(self):
        self._active = False
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass

    def address(self) -> str:
        return f"{get_local_ip()}:{self._port}"

    def send_to_device(self, device_id: str, message: dict):
        """Encola un mensaje para que el dispositivo lo recoja con GET /inbox/{id}."""
        with self._lock:
            self._outbox.setdefault(device_id, []).append(message)
        _log.info("→ [%s] mensaje encolado: %s", device_id, message)

    def connected_devices(self) -> dict:
        """Devuelve snapshot de los dispositivos IoT registrados."""
        with self._lock:
            return dict(self._iot_devices)

    # ── Hilo ───────────────────────────────────────────────────────────────────
    def run(self):
        if not self._enabled:
            _log.info("DeviceBridge desactivado en config.json")
            return

        bridge = self  # referencia para closures del handler

        class _Handler(BaseHTTPRequestHandler):

            def log_message(self, fmt, *args):
                _log.debug("[%s] %s %s", self.client_address[0], self.command, self.path)

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

            def _register(self, device_id: str, dtype: str, value: str):
                with bridge._lock:
                    bridge._iot_devices[device_id] = {
                        "last_seen":  datetime.now().isoformat(),
                        "type":       dtype,
                        "last_value": value,
                        "ip":         self.client_address[0],
                    }

            # ── GET ───────────────────────────────────────────────────────────
            def do_GET(self):

                if self.path == "/health":
                    with bridge._lock:
                        n = len(bridge._iot_devices)
                    self._send_json({
                        "status": "ok",
                        "service": "ardo-desktop",
                        "version": "2.0",
                        "devices_online": n,
                    })

                elif self.path == "/status":
                    states = bridge._get_status() if bridge._get_status else {}
                    self._send_json({"devices": states})

                elif self.path == "/devices":
                    with bridge._lock:
                        devs = dict(bridge._iot_devices)
                    self._send_json({"count": len(devs), "devices": devs})

                elif self.path.startswith("/inbox/"):
                    device_id = self.path[7:].strip("/")
                    if not device_id:
                        self._send_json({"error": "device_id requerido en URL"}, 400)
                        return
                    with bridge._lock:
                        messages = bridge._outbox.pop(device_id, [])
                    self._send_json({
                        "device_id": device_id,
                        "count":     len(messages),
                        "messages":  messages,
                    })

                else:
                    self._send_json({"error": "endpoint no encontrado"}, 404)

            # ── POST ──────────────────────────────────────────────────────────
            def do_POST(self):
                data = self._read_json()
                if data is None:
                    self._send_json({"error": "JSON inválido o Content-Length faltante"}, 400)
                    return

                # ── /command — texto natural ───────────────────────────────────
                if self.path == "/command":
                    text      = str(data.get("text", "")).strip()
                    device_id = str(data.get("device_id", self.client_address[0]))
                    if not text:
                        self._send_json({"error": "campo 'text' requerido"}, 400)
                        return

                    from tiny_nlu_provider import nlu_process
                    result = nlu_process(text)
                    self._register(device_id, "command", text)
                    bridge.command_received.emit(text)
                    _log.info("[%s] /command: %r → %s (%.0f%%)",
                              device_id, text, result["intent"], result["confidence"] * 100)
                    self._send_json({
                        "ok":         True,
                        "intent":     result["intent"],
                        "target":     result["target"],
                        "response":   result["response"],
                        "confidence": round(result["confidence"], 3),
                    })

                # ── /nlu — intent pre-procesado por el dispositivo ─────────────
                elif self.path == "/nlu":
                    intent    = str(data.get("intent", "")).upper().strip()
                    target    = str(data.get("target", "MAIN")).upper().strip()
                    device_id = str(data.get("device_id", self.client_address[0]))
                    if not intent:
                        self._send_json({"error": "campo 'intent' requerido"}, 400)
                        return
                    self._register(device_id, "nlu", f"{intent}/{target}")
                    bridge.nlu_received.emit(intent, target)
                    _log.info("[%s] /nlu: %s / %s", device_id, intent, target)
                    self._send_json({"ok": True, "intent": intent, "target": target})

                # ── /data — dato de sensor ─────────────────────────────────────
                elif self.path == "/data":
                    device_id = str(data.get("device_id", self.client_address[0])).strip()
                    dtype     = str(data.get("type",  "unknown")).strip()
                    value     = data.get("value", "")
                    unit      = str(data.get("unit", "")).strip()
                    if not device_id:
                        self._send_json({"error": "campo 'device_id' requerido"}, 400)
                        return
                    value_str = f"{value} {unit}".strip() if unit else str(value)
                    self._register(device_id, dtype, value_str)
                    bridge.data_received.emit(device_id, dtype, value_str)
                    _log.info("[%s] /data: %s = %s", device_id, dtype, value_str)
                    self._send_json({"ok": True, "device_id": device_id, "received": True})

                # ── /event — evento del dispositivo ───────────────────────────
                elif self.path == "/event":
                    device_id  = str(data.get("device_id", self.client_address[0])).strip()
                    event_name = str(data.get("event", "")).strip()
                    meta       = data.get("meta", {})
                    if not event_name:
                        self._send_json({"error": "campo 'event' requerido"}, 400)
                        return
                    meta_json = json.dumps(meta, ensure_ascii=False)
                    self._register(device_id, "event", event_name)
                    bridge.event_received.emit(device_id, event_name, meta_json)
                    _log.info("[%s] /event: %s | meta=%s", device_id, event_name, meta_json)
                    self._send_json({"ok": True, "event": event_name, "received": True})

                # ── /send/{device_id} — encolar mensaje para un dispositivo ───
                elif self.path.startswith("/send/"):
                    device_id = self.path[6:].strip("/")
                    if not device_id:
                        self._send_json({"error": "device_id requerido en URL"}, 400)
                        return
                    with bridge._lock:
                        bridge._outbox.setdefault(device_id, []).append(data)
                    _log.info("→ [%s] encolado: %s", device_id, data)
                    self._send_json({"ok": True, "device_id": device_id, "queued": True})

                else:
                    self._send_json({"error": "endpoint no encontrado"}, 404)

        try:
            server = _ThreadingHTTPServer((self._host, self._port), _Handler)
            server.timeout = 1.0
            self._server = server
            _log.info("DeviceBridge activo → http://%s:%d", get_local_ip(), self._port)
            while self._active:
                server.handle_request()
            server.server_close()
        except OSError as e:
            _log.error("DeviceBridge: puerto %d no disponible — %s", self._port, e)
        except Exception as e:
            _log.error("DeviceBridge: error inesperado — %s", e)
