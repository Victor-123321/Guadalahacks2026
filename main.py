"""
Ardo Desktop — Dashboard de domótica local con TinyNLU (ESP32-S3)
"""

import sys, asyncio, threading, io, re, os, time
from dataclasses import dataclass
from typing import Optional, List
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QPushButton, QLabel, QScrollArea, QFrame,
    QApplication, QStackedWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import (
    QFont, QColor, QPalette, QPainter, QBrush, QIcon
)

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    _MULTIMEDIA_OK = True
except ImportError:
    _MULTIMEDIA_OK = False

from ai_manager import AIManager
from utils import Logger, log_info, log_error
import datos
from memoria import MemoriaManager
from tools import ToolManager
from tts import ArdoTTS
from voice_listener import VoiceListener

logger = Logger()

# ─── Colores ──────────────────────────────────────────────────────────────────
C = {
    "bg":          "#070b12",   # fondo principal — azul muy oscuro
    "surface":     "#0b1120",   # sidebar y paneles
    "surface2":    "#0e1828",   # tarjetas y superficies
    "surface3":    "#132033",   # hover / elementos activos
    "border":      "#1b2e48",   # bordes sutiles
    "border2":     "#274468",   # bordes visibles
    "text":        "#d0e8ff",   # texto — blanco azulado
    "muted":       "#4d7aaa",   # texto secundario
    "dim":         "#243a5a",   # texto muy atenuado
    "accent":      "#1b7fe0",   # azul principal (botones, activo)
    "accent2":     "#3a9af5",   # azul claro (hover)
    "accent_dark": "#0b3a77",   # azul oscuro (fondos accent)
    "teal":        "#00b8d9",   # celeste (badges online / IoT)
    "teal_dark":   "#004a60",   # celeste oscuro
    "purple":      "#5b7fff",   # azul-violeta (device.set)
    "online":      "#4cc87a",   # verde (estado online)
    "error":       "#ff5555",   # rojo (errores)
    "warning":     "#ffb94a",   # naranja (advertencias)
    "card_on_bg":  "#071524",   # fondo tarjeta encendida
    "card_on_bdr": "#1b7fe0",   # borde tarjeta encendida
    "scroll":      "#1b2e48",   # scrollbar
}

QUICK_COMMANDS = [
    ("○", "enciende la luz del cuarto"),
    ("●", "apaga todas las luces"),
    ("△", "sube el aire a 20 grados"),
    ("▤", "cierra las persianas"),
    ("◎", "pon a limpiar el robot"),
]

# ─── Modelos de datos ─────────────────────────────────────────────────────────
@dataclass
class DeviceState:
    id:          str
    name:        str
    location:    str
    dtype:       str   # light | door | thermostat | curtain | robot | tv | fan | speaker | coffee
    icon:        str
    state:       bool  = False
    value:       Optional[str] = None

@dataclass
class RecentCmd:
    ts:          str
    text:        str
    badge:       str   # device.on | device.off | device.set | emergency | unknown
    intent:      str   = ""

INITIAL_DEVICES: List[DeviceState] = [
    DeviceState("light_main",    "Luz Sala",        "Sala",       "light",      "○",  True,  "80%"),
    DeviceState("light_bedroom", "Luz Cuarto",      "Dormitorio", "light",      "○",  False, None),
    DeviceState("fan_main",      "Ventilador",      "Sala",       "fan",        "✦",  False, None),
    DeviceState("thermostat",    "A/C Dormitorio",  "Dormitorio", "thermostat", "◈",  True,  "22°C"),
    DeviceState("curtain_main",  "Persianas",       "Sala",       "curtain",    "▤",  False, None),
    DeviceState("door_main",     "Cerradura",       "Entrada",    "door",       "◆",  True,  None),
    DeviceState("speaker_main",  "Altavoz",         "Cocina",     "speaker",    "▶",  False, None),
    DeviceState("coffee_main",   "Cafetera",        "Cocina",     "coffee",     "◑",  False, None),
]

def intent_to_badge(intent: str) -> str:
    on_intents  = {"LIGHT_ON","DOOR_OPEN","TV_ON","ROBOT_START","CURTAIN_OPEN"}
    off_intents = {"LIGHT_OFF","DOOR_CLOSE","TV_OFF","ROBOT_STOP","CURTAIN_CLOSE"}
    if intent in on_intents:        return "device.on"
    if intent in off_intents:       return "device.off"
    if intent == "THERMOSTAT":      return "device.set"
    if intent == "EMERGENCY":       return "emergency"
    if intent.startswith("CHAT_"):  return "chat"
    return "unknown"

def apply_nlu(result: dict, devices: List[DeviceState]):
    intent, target = result["intent"], result["target"]
    if intent.startswith("CHAT_"):
        return
    for d in devices:
        if intent == "LIGHT_ON":
            if d.id == "light_main"    and target in ("MAIN","ALL"):  d.state = True
            if d.id == "light_bedroom" and target in ("BEDROOM","ALL"): d.state = True
        elif intent == "LIGHT_OFF":
            if d.id == "light_main"    and target in ("MAIN","ALL"):  d.state = False
            if d.id == "light_bedroom" and target in ("BEDROOM","ALL"): d.state = False
        elif intent == "DOOR_OPEN"  and d.id == "door_main":    d.state = False
        elif intent == "DOOR_CLOSE" and d.id == "door_main":    d.state = True
        elif intent == "CURTAIN_OPEN"  and d.id == "curtain_main": d.state = False
        elif intent == "CURTAIN_CLOSE" and d.id == "curtain_main": d.state = True
        elif intent == "TV_ON"   and d.id.startswith("tv"):     d.state = True
        elif intent == "TV_OFF"  and d.id.startswith("tv"):     d.state = False
        elif intent == "ROBOT_START" and d.id == "robot_vacuum": d.state = True
        elif intent == "ROBOT_STOP"  and d.id == "robot_vacuum": d.state = False

# ─── Toggle Switch personalizado ─────────────────────────────────────────────
class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, state: bool = False, parent=None):
        super().__init__(parent)
        self._on = state
        self.setFixedSize(42, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(C["accent"] if self._on else C["border2"])
        p.setBrush(QBrush(bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, 42, 22, 11, 11)
        x = 22 if self._on else 2
        p.setBrush(QBrush(QColor("white")))
        p.drawEllipse(x, 2, 18, 18)
        p.end()

    def mousePressEvent(self, _):
        self._on = not self._on
        self.toggled.emit(self._on)
        self.update()

    def set_state(self, s: bool):
        self._on = s
        self.update()

# ─── Tarjeta de dispositivo ───────────────────────────────────────────────────
class DeviceCard(QFrame):
    toggle_requested = pyqtSignal(str, bool)

    def __init__(self, device: DeviceState, parent=None):
        super().__init__(parent)
        self.device = device
        self.setMinimumSize(170, 90)
        self.setMaximumHeight(110)
        self._build()
        self._refresh_style()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(4)

        # Fila superior: icono + toggle
        top = QHBoxLayout()
        top.setSpacing(8)

        icon_bg = QFrame()
        icon_bg.setFixedSize(32, 32)
        icon_bg.setStyleSheet(
            f"QFrame{{background:{C['surface3']};border-radius:8px;}}"
        )
        icon_lbl = QLabel(self.device.icon)
        icon_lbl.setFont(QFont("Segoe UI Symbol", 13))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("background:transparent;")
        icon_layout = QVBoxLayout(icon_bg)
        icon_layout.setContentsMargins(0,0,0,0)
        icon_layout.addWidget(icon_lbl)

        self.toggle = ToggleSwitch(self.device.state)
        self.toggle.toggled.connect(
            lambda s: self.toggle_requested.emit(self.device.id, s)
        )

        top.addWidget(icon_bg)
        top.addStretch()
        top.addWidget(self.toggle)
        outer.addLayout(top)

        # Nombre
        self.name_lbl = QLabel(self.device.name)
        self.name_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.name_lbl.setStyleSheet(f"color:{C['text']};background:transparent;")
        outer.addWidget(self.name_lbl)

        # Ubicación / valor
        sub_row = QHBoxLayout()
        sub_row.setSpacing(4)
        self.loc_lbl = QLabel(self.device.location)
        self.loc_lbl.setFont(QFont("Segoe UI", 8))
        self.loc_lbl.setStyleSheet(f"color:{C['muted']};background:transparent;")
        sub_row.addWidget(self.loc_lbl)
        sub_row.addStretch()
        self.val_lbl = QLabel(self.device.value or "")
        self.val_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.val_lbl.setStyleSheet(f"color:{C['accent']};background:transparent;")
        sub_row.addWidget(self.val_lbl)
        outer.addLayout(sub_row)

    def update_device(self, device: DeviceState):
        self.device = device
        self.toggle.set_state(device.state)
        self.val_lbl.setText(device.value or ("Encendido" if device.state else "Apagado")
                             if device.dtype != "door" else
                             ("Cerrada" if device.state else "Abierta"))
        self.val_lbl.setStyleSheet(
            f"color:{'#ff6b6b' if device.dtype == 'door' and device.state else C['accent']};"
            f"background:transparent;"
        )
        self._refresh_style()

    def _refresh_style(self):
        if self.device.state:
            bdr = "#ff4a6e" if self.device.dtype == "door" else C["card_on_bdr"]
            bg  = "#1f0a15" if self.device.dtype == "door" else C["card_on_bg"]
        else:
            bdr, bg = C["border"], C["surface2"]
        self.setStyleSheet(
            f"DeviceCard{{background:{bg};border:1px solid {bdr};"
            f"border-radius:12px;}}"
        )

# ─── Chip de comando rápido ───────────────────────────────────────────────────
class QuickChip(QPushButton):
    def __init__(self, icon: str, text: str, parent=None):
        super().__init__(f"  {icon}  {text}", parent)
        self.setFont(QFont("Segoe UI", 9))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self.setStyleSheet(
            f"QPushButton{{background:{C['surface2']};color:{C['text']};"
            f"border:1px solid {C['border2']};border-radius:14px;"
            f"padding:0 14px;}}"
            f"QPushButton:hover{{background:{C['surface3']};border-color:{C['accent']};"
            f"color:{C['accent']};}}"
        )

# ─── Fila de comando reciente ─────────────────────────────────────────────────
BADGE_COLORS = {
    "device.on":  (C["teal"],    C["teal_dark"]),
    "device.off": (C["muted"],   C["surface3"]),
    "device.set": (C["purple"],  "#2a2060"),
    "emergency":  (C["error"],   "#3a0808"),
    "chat":       (C["accent2"], C["accent_dark"]),
    "unknown":    (C["dim"],     C["surface2"]),
}

class RecentRow(QFrame):
    def __init__(self, cmd: RecentCmd, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame{{background:{C['surface2']};border-radius:8px;"
            f"border:1px solid {C['border']};}}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(12)

        ts = QLabel(cmd.ts)
        ts.setFont(QFont("Segoe UI", 9))
        ts.setStyleSheet(f"color:{C['muted']};background:transparent;")
        ts.setFixedWidth(38)

        txt = QLabel(cmd.text)
        txt.setFont(QFont("Segoe UI", 10))
        txt.setStyleSheet(f"color:{C['text']};background:transparent;")

        fg, bg = BADGE_COLORS.get(cmd.badge, BADGE_COLORS["unknown"])
        badge = QLabel(cmd.badge)
        badge.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        badge.setFixedHeight(20)
        badge.setStyleSheet(
            f"color:{fg};background:{bg};border-radius:4px;padding:0 6px;"
        )
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(ts)
        layout.addWidget(txt, 1)
        layout.addWidget(badge)

# ─── Widget de cara ───────────────────────────────────────────────────────────
FACE_DIR = Path(__file__).parent / "ardo_faces"

FACE_VIDEOS = {
    "pensando":   "aldo_pensando.mp4",
    "ejecutando": "aldo_ejecutando_comando.mp4",
    "esperando":  "aldo_esperando.mp4",
}
FACE_FALLBACK = {
    "pensando":   "◎",
    "ejecutando": "▶",
    "esperando":  "○",
}

class FaceWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(186, 200)
        self.setStyleSheet(
            f"QFrame{{background:{C['surface3']};border-radius:16px;border:none;}}"
        )
        self._state = "esperando"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if _MULTIMEDIA_OK:
            self.vid = QVideoWidget()
            self.vid.setFixedSize(186, 200)
            self.vid.setStyleSheet("background:transparent;border:none;")
            layout.addWidget(self.vid)
            self._player = QMediaPlayer()
            self._audio  = QAudioOutput()
            self._audio.setVolume(0)
            self._player.setAudioOutput(self._audio)
            self._player.setVideoOutput(self.vid)
            self._player.mediaStatusChanged.connect(self._loop)
        else:
            self.vid, self._player = None, None

        self._fallback = QLabel("○")
        self._fallback.setFont(QFont("Segoe UI Symbol", 56))
        self._fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fallback.setStyleSheet("background:transparent;border:none;")
        if _MULTIMEDIA_OK:
            self._fallback.hide()
        layout.addWidget(self._fallback)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.set_state("esperando"))
        self._load("esperando")

    def _loop(self, status):
        if self._player and status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._player.setPosition(0); self._player.play()

    def _load(self, state: str):
        fname = FACE_VIDEOS.get(state, FACE_VIDEOS["esperando"])
        path  = FACE_DIR / fname
        if self._player:
            self._player.stop()
        if path.exists() and _MULTIMEDIA_OK and self._player:
            self._fallback.hide(); self.vid.show()
            self._player.setSource(QUrl.fromLocalFile(str(path)))
            self._player.play()
            return
        self._fallback.setText(FACE_FALLBACK.get(state, "○"))
        if self.vid: self.vid.hide()
        self._fallback.show()

    def set_state(self, state: str, ms: int = 0):
        if state != self._state:
            self._state = state
            self._load(state)
        if ms > 0:
            self._timer.start(ms)
        else:
            self._timer.stop()

# ─── Voz ──────────────────────────────────────────────────────────────────────
class VoiceEngine:
    def __init__(self):
        self._enabled   = True
        self._lock      = threading.Lock()
        self._stop_flag = False
        self._tts = None
        try:
            self._tts = ArdoTTS()
        except Exception as e:
            log_error(f"VoiceEngine Kokoro init: {e}")

    def speak(self, text: str):
        if not self._enabled or not self._tts: return
        clean = re.sub(r'[^\w\s,.!?áéíóúüñ¿¡`]', '', text, flags=re.UNICODE).strip()[:300]
        if clean:
            self._stop_flag = False
            threading.Thread(target=self._run, args=(clean,), daemon=True).start()

    def stop(self):
        """Interrumpe la reproducción en curso (barge-in)."""
        import sounddevice as sd
        self._stop_flag = True
        try:
            sd.stop()
        except Exception:
            pass

    def _run(self, text):
        with self._lock:
            tmp_path = None
            try:
                import tempfile
                import soundfile as sf
                import sounddevice as sd
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp.close()
                tmp_path = tmp.name
                self._tts.generate_to_file(text, tmp_path)
                if not self._stop_flag:
                    data, rate = sf.read(tmp_path)
                    sd.play(data, rate)
                    sd.wait()   # bloquea hasta terminar; sd.stop() lo interrumpe
            except Exception as e:
                log_error(f"VoiceEngine speak: {e}")
            finally:
                self._stop_flag = False
                if tmp_path and os.path.exists(tmp_path):
                    try: os.unlink(tmp_path)
                    except Exception: pass

    def toggle(self) -> bool: self._enabled = not self._enabled; return self._enabled
    @property
    def available(self): return self._tts is not None

# ─── AI Worker ────────────────────────────────────────────────────────────────
class AIWorker(QThread):
    response_ready = pyqtSignal(str, float)   # response_text, latency_ms
    error_occurred = pyqtSignal(str)

    def __init__(self, ai_manager, message: str):
        super().__init__(); self.ai = ai_manager; self.msg = message

    def run(self):
        try:
            loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
            t0 = time.perf_counter()
            try:
                resp = loop.run_until_complete(self.ai.chat(self.msg, provider="auto"))
            finally: loop.close()
            ms = (time.perf_counter() - t0) * 1000
            self.response_ready.emit(resp or "", ms)
        except Exception as e:
            log_error(f"AIWorker: {e}"); self.error_occurred.emit(str(e))

# ─── Ventana principal ────────────────────────────────────────────────────────
class ArdoDesktopWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ai       = AIManager()
        self.voice    = VoiceEngine()
        self.memoria  = MemoriaManager()
        self.tools    = ToolManager()
        self.devices: List[DeviceState] = [
            DeviceState(d.id, d.name, d.location, d.dtype, d.icon, d.state, d.value)
            for d in INITIAL_DEVICES
        ]
        self.recent: List[RecentCmd] = []
        self._latency_ms: float = 0.0
        self._ai_worker = None
        self._init_ui()
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(8000)
        self._idle_timer.timeout.connect(lambda: self.face.set_state("esperando"))
        self._idle_timer.start()
        # Verificar conectividad a Ollama, HA y STT tras 2 s
        QTimer.singleShot(2000, self._check_connections)
        # Arrancar escucha de micrófono
        self._voice = VoiceListener()
        self._voice.transcription_ready.connect(self._on_voice_transcription)
        self._voice.state_changed.connect(self._on_voice_state)
        self._voice.error_occurred.connect(self._on_voice_error)
        self._voice.barge_in.connect(self.voice.stop)
        self._voice.start()
        log_info("Ardo Desktop iniciado")

    # ── UI raíz ────────────────────────────────────────────────────────────────
    def _init_ui(self):
        self.setWindowTitle("Ardo Desktop  ·  NLU Local")
        self.setGeometry(60, 40, 1180, 820)
        self.setMinimumSize(900, 620)
        self.setStyleSheet(f"QMainWindow,QWidget{{background:{C['bg']};}}")
       
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ardo_faces", "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        
        central = QWidget(); self.setCentralWidget(central)
        root = QHBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_main(), 1)
        self._update_status_panel()

    # ── Sidebar ────────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        sb = QFrame(); sb.setFixedWidth(210)
        sb.setStyleSheet(f"QFrame{{background:{C['surface']};border-right:1px solid {C['border']};}}")
        lay = QVBoxLayout(sb); lay.setContentsMargins(14, 20, 14, 16); lay.setSpacing(4)

        # Logo
        row = QHBoxLayout(); row.setSpacing(8)
        icon_lbl = QLabel("◈"); icon_lbl.setFont(QFont("Segoe UI Symbol", 18))
        icon_lbl.setStyleSheet(f"color:{C['teal']};background:transparent;")
        tc = QVBoxLayout(); tc.setSpacing(0)
        personaje = datos.get_personaje(datos.get_bot().get("personaje_default","Ardo"))
        self._name_lbl = QLabel(personaje.get("nombre","Ardo"))
        self._name_lbl.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self._name_lbl.setStyleSheet(f"color:{C['text']};background:transparent;")
        sub = QLabel("NLU Local · v2"); sub.setFont(QFont("Segoe UI", 8))
        sub.setStyleSheet(f"color:{C['muted']};background:transparent;")
        tc.addWidget(self._name_lbl); tc.addWidget(sub)
        row.addWidget(icon_lbl); row.addLayout(tc, 1); lay.addLayout(row)

        def _sep():
            s = QFrame(); s.setFrameShape(QFrame.Shape.HLine)
            s.setStyleSheet(f"background:{C['border']};margin:6px 0;"); s.setFixedHeight(1)
            return s

        lay.addWidget(_sep())

        # Motor de comandos
        sec = QLabel("MOTOR DE COMANDOS"); sec.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        sec.setStyleSheet(f"color:{C['dim']};background:transparent;padding:2px 2px 2px 4px;")
        lay.addWidget(sec)

        self._sidebar_badges: dict = {}
        for key, name, sub_txt, active in [
            ("nlu", "Ardo NLU",        "TinyNLU · ESP32-S3",        True),
            ("llm", "Ollama LLM",      "qwen2.5:7b · 192.168.12.1", False),
            ("ha",  "Home Assistant",  "192.168.12.1:8123",          False),
            ("stt", "Whisper STT",     "192.168.12.1:6767",          False),
        ]:
            item = QFrame()
            item.setStyleSheet(
                f"QFrame{{background:{'#0d2b22' if active else 'transparent'};"
                f"border-radius:8px;{'border-left:3px solid '+C['teal']+';' if active else ''}}}"
            )
            il = QHBoxLayout(item); il.setContentsMargins(8,6,8,6); il.setSpacing(8)
            tc2 = QVBoxLayout(); tc2.setSpacing(0)
            nl = QLabel(name); nl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold if active else QFont.Weight.Normal))
            nl.setStyleSheet(f"color:{C['teal'] if active else C['muted']};background:transparent;")
            sl = QLabel(sub_txt); sl.setFont(QFont("Segoe UI", 8))
            sl.setStyleSheet(f"color:{C['dim']};background:transparent;")
            tc2.addWidget(nl); tc2.addWidget(sl); il.addLayout(tc2, 1)
            badge = QLabel("ON" if active else "…")
            badge.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            badge.setFixedSize(26, 16)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                f"color:white;background:{C['teal'] if active else C['dim']};border-radius:4px;"
            )
            il.addWidget(badge)
            self._sidebar_badges[key] = badge
            lay.addWidget(item)

        lay.addStretch()

        # Cara
        self.face = FaceWidget()
        face_row = QHBoxLayout(); face_row.setContentsMargins(0,0,0,0)
        face_row.addStretch(); face_row.addWidget(self.face); face_row.addStretch()
        lay.addLayout(face_row)

        lay.addSpacing(8)

        # Panel de estado
        status_frame = QFrame()
        status_frame.setStyleSheet(
            f"QFrame{{background:{C['surface2']};border-radius:10px;border:1px solid {C['border']};}}"
        )
        sl2 = QVBoxLayout(status_frame); sl2.setContentsMargins(10,8,10,8); sl2.setSpacing(4)
        self._status_rows = {}
        for key, label, default_val, default_color in [
            ("estado",      "Estado",       "En línea",   C["online"]),
            ("dispositivos","Dispositivos", "0/0 on",     C["text"]),
            ("latencia",    "Latencia NLU", "— ms",       C["teal"]),
            ("modo",        "Modo",         "100% offline",C["muted"]),
        ]:
            row_w = QHBoxLayout(); row_w.setSpacing(0)
            k = QLabel(label); k.setFont(QFont("Segoe UI", 8))
            k.setStyleSheet(f"color:{C['muted']};background:transparent;")
            v = QLabel(default_val); v.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            v.setStyleSheet(f"color:{default_color};background:transparent;")
            v.setAlignment(Qt.AlignmentFlag.AlignRight)
            row_w.addWidget(k); row_w.addStretch(); row_w.addWidget(v)
            sl2.addLayout(row_w)
            self._status_rows[key] = v
        lay.addWidget(status_frame)
        lay.addWidget(_sep())

        # Botones de acción
        for icon, label, slot in [
            ("▤", "Memoria",      self._show_memoria),
            ("◈", "Herramientas", self._show_tools),
            ("×", "Limpiar",      self._clear_recent),
        ]:
            btn = QPushButton(f"  {icon}  {label}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFont(QFont("Segoe UI", 10)); btn.setFixedHeight(34)
            btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:{C['muted']};"
                f"border:none;border-radius:6px;text-align:left;padding-left:6px;}}"
                f"QPushButton:hover{{background:{C['surface2']};color:{C['text']};}}"
            )
            btn.clicked.connect(slot); lay.addWidget(btn)

        if self.voice.available:
            self._voice_btn = QPushButton("  ▶  Voz: OFF")
            self._voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._voice_btn.setFont(QFont("Segoe UI", 10))
            self._voice_btn.setFixedHeight(34)
            self._voice_btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:{C['muted']};"
                f"border:none;border-radius:6px;text-align:left;padding-left:6px;}}"
                f"QPushButton:hover{{background:{C['surface2']};color:{C['text']};}}"
            )
            self._voice_btn.clicked.connect(self._toggle_voice); lay.addWidget(self._voice_btn)

        return sb

    # ── Main area ──────────────────────────────────────────────────────────────
    def _build_main(self):
        main = QFrame(); main.setStyleSheet(f"QFrame{{background:{C['bg']};border:none;}}")
        lay = QVBoxLayout(main); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        lay.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget{background:transparent;}")
        self.stack.addWidget(self._build_dashboard_page())   # index 0
        self.stack.addWidget(self._build_info_page())          # index 1
        lay.addWidget(self.stack, 1)
        lay.addWidget(self._build_input_bar())
        return main

    def _build_topbar(self):
        bar = QFrame(); bar.setFixedHeight(54)
        bar.setStyleSheet(f"QFrame{{background:{C['surface']};border-bottom:1px solid {C['border']};}}")
        lay = QHBoxLayout(bar); lay.setContentsMargins(20,0,20,0)

        icon = QLabel("◈"); icon.setFont(QFont("Segoe UI Symbol", 16))
        icon.setStyleSheet(f"color:{C['teal']};background:transparent;")
        title = QLabel("Ardo NLU"); title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{C['teal']};background:transparent;")
        desc = QLabel("Motor de Comandos Local"); desc.setFont(QFont("Segoe UI", 10))
        desc.setStyleSheet(f"color:{C['muted']};background:transparent;")
        esp_badge = QLabel("ESP32-S3"); esp_badge.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        esp_badge.setStyleSheet(
            f"color:{C['teal']};background:{C['teal_dark']};border-radius:5px;padding:2px 7px;"
        )

        lay.addWidget(icon); lay.addSpacing(8); lay.addWidget(title)
        lay.addSpacing(8); lay.addWidget(desc); lay.addSpacing(8); lay.addWidget(esp_badge)
        lay.addStretch()

        self._status_badge = QLabel("● Listo")
        self._status_badge.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._status_badge.setStyleSheet(
            f"color:{C['online']};background:#0d2a18;border-radius:8px;padding:3px 10px;"
        )
        lay.addWidget(self._status_badge)
        return bar

    def _build_dashboard_page(self):
        page = QFrame(); page.setStyleSheet("QFrame{background:transparent;}")
        lay = QVBoxLayout(page); lay.setContentsMargins(0,0,0,0)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:transparent;}}"
            f"QScrollBar:vertical{{border:none;background:{C['surface']};width:6px;border-radius:3px;}}"
            f"QScrollBar::handle:vertical{{background:{C['scroll']};border-radius:3px;min-height:20px;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
        )
        container = QWidget(); container.setStyleSheet("background:transparent;")
        inner = QVBoxLayout(container); inner.setContentsMargins(20,16,20,16); inner.setSpacing(20)

        # Chips rápidos
        chips_row = QHBoxLayout(); chips_row.setSpacing(8)
        for icon, txt in QUICK_COMMANDS:
            chip = QuickChip(icon, txt)
            chip.clicked.connect(lambda _, t=txt: self._process_command(t))
            chips_row.addWidget(chip)
        chips_row.addStretch()
        inner.addLayout(chips_row)

        # Dispositivos
        dev_header = QHBoxLayout()
        dev_title = QLabel("DISPOSITIVOS DETECTADOS")
        dev_title.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        dev_title.setStyleSheet(f"color:{C['muted']};background:transparent;")
        self._dev_count_lbl = QLabel(f"· {len(self.devices)}")
        self._dev_count_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._dev_count_lbl.setStyleSheet(f"color:{C['teal']};background:transparent;")
        dev_header.addWidget(dev_title); dev_header.addWidget(self._dev_count_lbl)
        dev_header.addStretch()
        inner.addLayout(dev_header)

        self._cards_grid = QGridLayout(); self._cards_grid.setSpacing(10)
        self._device_cards: dict[str, DeviceCard] = {}
        for i, dev in enumerate(self.devices):
            card = DeviceCard(dev)
            card.toggle_requested.connect(self._on_device_toggled)
            self._device_cards[dev.id] = card
            self._cards_grid.addWidget(card, i // 4, i % 4)
        inner.addLayout(self._cards_grid)

        # Comandos recientes
        rec_header = QLabel("COMANDOS RECIENTES")
        rec_header.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        rec_header.setStyleSheet(f"color:{C['muted']};background:transparent;")
        inner.addWidget(rec_header)

        self._recent_layout = QVBoxLayout(); self._recent_layout.setSpacing(6)
        placeholder = QLabel("Aún no hay comandos. Escribe uno abajo.")
        placeholder.setFont(QFont("Segoe UI", 10))
        placeholder.setStyleSheet(f"color:{C['dim']};background:transparent;padding:10px;")
        self._recent_layout.addWidget(placeholder)
        self._recent_placeholder = placeholder
        inner.addLayout(self._recent_layout)
        inner.addStretch()

        scroll.setWidget(container)
        lay.addWidget(scroll)
        self._dashboard_scroll = scroll
        return page

    def _build_info_page(self):
        page = QFrame(); page.setStyleSheet("QFrame{background:transparent;}")
        lay = QVBoxLayout(page); lay.setContentsMargins(20,20,20,20)
        self._info_lbl = QLabel("")
        self._info_lbl.setFont(QFont("Segoe UI", 11))
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setStyleSheet(
            f"color:{C['text']};background:{C['surface2']};border-radius:12px;"
            f"border:1px solid {C['border']};padding:16px;"
        )
        self._info_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self._info_lbl); lay.addStretch()
        return page

    def _build_input_bar(self):
        bar = QFrame(); bar.setFixedHeight(72)
        bar.setStyleSheet(
            f"QFrame{{background:{C['surface']};border-top:1px solid {C['border']};}}"
        )
        lay = QHBoxLayout(bar); lay.setContentsMargins(20,12,20,8); lay.setSpacing(10)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Escribe un comando…  ej: enciende la luz del cuarto")
        self.input_field.setFont(QFont("Segoe UI", 11)); self.input_field.setFixedHeight(42)
        self.input_field.setStyleSheet(
            f"QLineEdit{{background:{C['surface2']};border:1px solid {C['border2']};"
            f"border-radius:20px;padding:0 18px;color:{C['text']};}}"
            f"QLineEdit:focus{{border:1px solid {C['accent']};background:{C['surface3']};}}"
        )
        self.input_field.returnPressed.connect(self._on_send)

        self.send_btn = QPushButton("→")
        self.send_btn.setFixedSize(42,42)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self.send_btn.setStyleSheet(
            f"QPushButton{{background:{C['accent']};color:white;border:none;border-radius:21px;}}"
            f"QPushButton:hover{{background:{C['accent2']};}}"
            f"QPushButton:disabled{{background:{C['border']};color:{C['dim']};}}"
        )
        self.send_btn.clicked.connect(self._on_send)

        shortcuts = QLabel("Enter → enviar")
        shortcuts.setFont(QFont("Segoe UI", 8))
        shortcuts.setStyleSheet(f"color:{C['dim']};background:transparent;")

        self._lat_lbl = QLabel("—  ms")
        self._lat_lbl.setFont(QFont("Segoe UI", 8))
        self._lat_lbl.setStyleSheet(f"color:{C['teal']};background:transparent;")

        lay.addWidget(self.input_field, 1)
        lay.addWidget(self.send_btn)

        bot_row = QHBoxLayout()
        bot_row.addWidget(shortcuts); bot_row.addStretch(); bot_row.addWidget(self._lat_lbl)

        wrapper = QVBoxLayout(); wrapper.setContentsMargins(0,0,0,0); wrapper.setSpacing(2)
        input_row = QHBoxLayout()
        input_row.addWidget(self.input_field, 1); input_row.addWidget(self.send_btn)

        bar_inner = QFrame(); bar_inner.setStyleSheet("background:transparent;border:none;")
        bi_lay = QVBoxLayout(bar_inner); bi_lay.setContentsMargins(0,0,0,0); bi_lay.setSpacing(3)
        bi_lay.addLayout(input_row); bi_lay.addLayout(bot_row)

        # Re-build the bar layout properly
        for i in reversed(range(lay.count())): lay.itemAt(i).widget() and lay.itemAt(i).widget().deleteLater()
        # Just use a simple single-level layout
        bar2 = QFrame(); bar2.setFixedHeight(72)
        bar2.setStyleSheet(
            f"QFrame{{background:{C['surface']};border-top:1px solid {C['border']};}}"
        )
        b2l = QVBoxLayout(bar2); b2l.setContentsMargins(20,10,20,6); b2l.setSpacing(3)
        self.mic_btn = QPushButton("🎤")
        self.mic_btn.setFixedSize(42, 42)
        self.mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_btn.setFont(QFont("Segoe UI", 16))
        self.mic_btn.setToolTip("Activar / silenciar micrófono")
        self.mic_btn.setCheckable(True)
        self.mic_btn.setChecked(True)
        self._apply_mic_style(active=True)
        self.mic_btn.clicked.connect(self._on_mic_toggle)

        row1 = QHBoxLayout(); row1.setSpacing(10)
        row1.addWidget(self.mic_btn)
        row1.addWidget(self.input_field, 1)
        row1.addWidget(self.send_btn)
        row2 = QHBoxLayout()
        row2.addWidget(shortcuts); row2.addStretch(); row2.addWidget(self._lat_lbl)
        b2l.addLayout(row1); b2l.addLayout(row2)
        return bar2

    # ── Lógica de comandos ─────────────────────────────────────────────────────
    def _on_send(self):
        text = self.input_field.text().strip()
        if not text: return
        self.input_field.clear()
        self._process_command(text)

    def _process_command(self, text: str):
        self.stack.setCurrentIndex(0)
        self._idle_timer.stop()
        self.face.set_state("pensando")
        self._set_status("Procesando…", C["warning"])
        self.input_field.setEnabled(False); self.send_btn.setEnabled(False)

        # Herramientas locales (búsqueda web, control de luces, etc.)
        tool = self.tools.detectar_y_ejecutar(text)
        if tool:
            self._add_recent(text, "device.on" if tool.ok else "unknown", "TOOL")
            self.face.set_state("ejecutando", ms=4000)
            self._set_status("Listo", C["online"])
            self._info_lbl.setText(tool.mensaje)
            self.voice.speak(tool.mensaje)
            self.stack.setCurrentIndex(1)
            self._idle_timer.start()
            self.input_field.setEnabled(True)
            self.send_btn.setEnabled(True)
            return

        self._ai_worker = AIWorker(self.ai, text)
        self._ai_worker.response_ready.connect(self._on_response)
        self._ai_worker.error_occurred.connect(self._on_error)
        self._ai_worker.start()
        self._pending_text = text

    def _on_response(self, ai_response: str, latency_ms: float):
        from tiny_nlu_provider import nlu_process
        result = nlu_process(self._pending_text)
        apply_nlu(result, self.devices)
        badge  = intent_to_badge(result["intent"])

        self._add_recent(self._pending_text, badge, result["intent"])
        self._refresh_device_cards()
        self._update_status_panel()

        self._latency_ms = latency_ms
        self._lat_lbl.setText(f"~{latency_ms:.0f} ms  ·  {result['confidence']:.0%} conf")
        self._status_rows["latencia"].setText(f"~{latency_ms:.0f} ms")

        is_iot = result["intent"] not in ("UNKNOWN",) and not result["intent"].startswith("CHAT_")
        self.face.set_state("ejecutando" if is_iot else "pensando", ms=5000)
        self._idle_timer.start()
        self._set_status("Listo", C["online"])

        # Para UNKNOWN el AIWorker ya llamó a Ollama; usar esa respuesta en TTS
        speak_text = ai_response if result["intent"] == "UNKNOWN" else result["response"]
        self.voice.speak(speak_text)

        # Llamar HA bridge en hilo de fondo para no bloquear la UI
        if is_iot:
            threading.Thread(
                target=self._execute_ha_command,
                args=(result["intent"], result["target"]),
                daemon=True,
            ).start()

        self.input_field.setEnabled(True); self.send_btn.setEnabled(True)

    def _on_error(self, _error: str):
        self._add_recent("Error de procesamiento", "unknown", "ERROR")
        self.face.set_state("esperando")
        self._idle_timer.start()
        self._set_status("Error", C["error"])
        self.input_field.setEnabled(True); self.send_btn.setEnabled(True)

    def _add_recent(self, text: str, badge: str, intent: str):
        cmd = RecentCmd(ts=datetime.now().strftime("%H:%M"), text=text,
                        badge=badge, intent=intent)
        self.recent.insert(0, cmd)
        if len(self.recent) > 20: self.recent.pop()
        self._refresh_recent_list()

    def _refresh_recent_list(self):
        while self._recent_layout.count():
            item = self._recent_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        if not self.recent:
            self._recent_layout.addWidget(self._recent_placeholder)
            return
        for cmd in self.recent[:10]:
            self._recent_layout.addWidget(RecentRow(cmd))

    def _refresh_device_cards(self):
        for dev in self.devices:
            if dev.id in self._device_cards:
                self._device_cards[dev.id].update_device(dev)

    # ── Voz ────────────────────────────────────────────────────────────────────
    def _on_voice_transcription(self, text: str):
        """Texto llegado del STT → entra al pipeline como si el usuario lo hubiera escrito."""
        self.input_field.setText(text)
        self._process_command(text)

    def _on_voice_state(self, state: str):
        """Actualiza UI según el estado del micrófono."""
        if state == "grabando":
            self.face.set_state("pensando")
            self._set_status("Escuchando voz…", C["teal"])
            self._sidebar_badges.get("stt") and self._sidebar_badges["stt"].setText("REC")
        elif state == "procesando":
            self._set_status("Transcribiendo…", C["warning"])
        elif state == "escuchando":
            self._set_status("Listo", C["online"])
            b = self._sidebar_badges.get("stt")
            if b and b.text() == "REC":
                b.setText("ON")

    def _on_voice_error(self, error: str):
        log_error(f"[STT] {error}")

    def _on_mic_toggle(self):
        muted = self._voice.toggle_mute()
        self._apply_mic_style(active=not muted)

    def _apply_mic_style(self, active: bool):
        if not hasattr(self, "mic_btn"):
            return
        if active:
            self.mic_btn.setText("🎤")
            self.mic_btn.setStyleSheet(
                f"QPushButton{{background:{C['teal_dark']};color:{C['teal']};"
                f"border:1px solid {C['teal']};border-radius:21px;}}"
                f"QPushButton:hover{{background:{C['teal']};color:white;}}"
            )
        else:
            self.mic_btn.setText("🔇")
            self.mic_btn.setStyleSheet(
                f"QPushButton{{background:{C['surface3']};color:{C['muted']};"
                f"border:1px solid {C['border']};border-radius:21px;}}"
                f"QPushButton:hover{{background:{C['border2']};color:{C['text']};}}"
            )

    def _execute_ha_command(self, intent: str, target: str):
        """Llama al HA bridge en un hilo de fondo para no bloquear la UI."""
        from ha_bridge import execute_nlu_command
        ok = execute_nlu_command(intent, target)
        log_info(f"[HA] {intent}/{target} → {'OK' if ok else 'FAIL'}")

    def _check_connections(self):
        """Comprueba Ollama, HA y STT en background; actualiza badges del sidebar."""
        def _check():
            from ha_bridge import is_connected as ha_ok
            import requests as _req
            ha_up     = ha_ok()
            ollama_up = self.ai.ollama_available()
            stt_base = "http://192.168.12.1:6767"
            try:
                r = _req.get(f"{stt_base}/v1/models", timeout=3)
                stt_up = r.status_code == 200
            except Exception:
                stt_up = False
            QTimer.singleShot(0, lambda: self._apply_connection_status(ollama_up, ha_up, stt_up))
        threading.Thread(target=_check, daemon=True).start()

    def _apply_connection_status(self, ollama_up: bool, ha_up: bool, stt_up: bool = False):
        for key, up in [("llm", ollama_up), ("ha", ha_up), ("stt", stt_up)]:
            badge = self._sidebar_badges.get(key)
            if badge:
                badge.setText("ON" if up else "OFF")
                color = C["teal"] if up else C["error"]
                badge.setStyleSheet(f"color:white;background:{color};border-radius:4px;")

    def _on_device_toggled(self, device_id: str, new_state: bool):
        for dev in self.devices:
            if dev.id == device_id: dev.state = new_state; break
        self._refresh_device_cards()
        self._update_status_panel()

    def _update_status_panel(self):
        on_count = sum(1 for d in self.devices if d.state)
        total = len(self.devices)
        self._status_rows["dispositivos"].setText(f"{on_count}/{total} on")
        self._dev_count_lbl.setText(f"· {total}")

    def _set_status(self, text: str, color: str):
        icon = "●" if color == C["online"] else ("◌" if color == C["warning"] else "✕")
        self._status_badge.setText(f"{icon} {text}")
        self._status_badge.setStyleSheet(
            f"color:{color};background:{'#0d2a18' if color==C['online'] else C['surface2']};"
            f"border-radius:8px;padding:3px 10px;"
        )

    # ── Botones sidebar ────────────────────────────────────────────────────────
    def _show_memoria(self):
        self._info_lbl.setText(self.memoria._cmd_listar())
        self.stack.setCurrentIndex(1)

    def _show_tools(self):
        self._info_lbl.setText(self.tools.listar_disponibles())
        self.stack.setCurrentIndex(1)

    def _clear_recent(self):
        self.recent.clear(); self._refresh_recent_list()

    def _toggle_voice(self):
        on = self.voice.toggle()
        self._voice_btn.setText(f"  ▶  Voz: {'ON' if on else 'OFF'}")

    def closeEvent(self, event):
        if hasattr(self, "memoria"):
            stats = self.memoria.get_stats()
            self.memoria.cerrar_sesion(
                f"Sesión del {datetime.now().strftime('%d/%m/%Y')}. "
                f"Mensajes: {stats.get('total_mensajes',0)}."
            )
        if hasattr(self, "face") and self.face._player:
            self.face._player.stop()
        event.accept()


# ─── Entry point ──────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Ardo Desktop")
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ArdoDesktop.v2")
    except Exception: pass
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,     QColor(C["bg"]))
    p.setColor(QPalette.ColorRole.WindowText, QColor(C["text"]))
    p.setColor(QPalette.ColorRole.Base,       QColor(C["surface"]))
    p.setColor(QPalette.ColorRole.Text,       QColor(C["text"]))
    app.setPalette(p)
    w = ArdoDesktopWindow(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
