"""
Ardo Desktop — Interfaz gráfica local para el motor TinyNLU del ESP2 (Ardo v2)
GUI adaptada de Lune CD. Backend: TinyNLU, 100% offline.
"""

import sys
import asyncio
import threading
import io
import re
import subprocess
import os
import json
import importlib
import random
from PyQt6.QtGui import QPainter
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel, QScrollArea, QFrame,
    QApplication, QMessageBox, QStackedWidget, QTextEdit,
    QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QUrl
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon, QPixmap

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    _MULTIMEDIA_OK = True
except ImportError:
    _MULTIMEDIA_OK = False

from config import Config
from ai_manager import AIManager
from utils import Logger, log_info, log_error
import datos
from memoria import MemoriaManager
from tools import ToolManager

logger = Logger()

# ─── Colores ──────────────────────────────────────────────────────────────────
COLORS = {
    "bg":           "#0e0f14",
    "surface":      "#161820",
    "surface2":     "#1e2130",
    "surface3":     "#252840",
    "border":       "#2a2d45",
    "border2":      "#353860",
    "text":         "#e8eaf6",
    "text_muted":   "#7b7fa8",
    "text_dim":     "#4a4e72",
    "accent":       "#00c2a0",       # verde-teal para Ardo
    "accent2":      "#00e5c0",
    "accent_dark":  "#007a65",
    "success":      "#69db7c",
    "error":        "#ff6b6b",
    "warning":      "#ffd166",
    "user_bubble":  "#2a2d4a",
    "bot_bubble":   "#1e2035",
    "scrollbar":    "#2a2d45",
}

PROVIDER_META = {
    "tiny_nlu": {
        "label":  "Ardo NLU",
        "icon":   "🤖",
        "color":  COLORS["accent"],
        "dark":   COLORS["accent_dark"],
        "desc":   "Motor de Comandos Local · ESP32-S3",
        "system": lambda: "",
    },
}

def _get_system_prompt():
    return datos.get_personaje(
        datos.get_bot().get("personaje_default", "Ardo")
    ).get("systemPrompt", "")


# ─── Cara de Ardo ─────────────────────────────────────────────────────────────
FACE_DIR = Path(__file__).parent / "lune_face"

FACE_FILES = {
    "normal":   ("lune_normal.png",   "image"),
    "happy":    ("lune_happy.png",    "image"),
    "thinking": ("pensando.mp4",      "video"),
    "typing":   ("escribiendo.mp4",   "video"),
    "reading":  ("lune_reading.png",  "image"),
    "sad":      ("lune_sad.png",      "image"),
    "confused": ("lune_confused.png", "image"),
    "error":    ("lune_error.png",    "image"),
}

FACE_FALLBACK_IMAGE = {
    "thinking": "lune_thinking.png",
    "typing":   "lune_typing.png",
}

EMOTION_KEYWORDS = {
    "happy":   ["encendida","apagada","abriendo","cerrando","en marcha","detenido","entendido","listo"],
    "error":   ["❌","error:","no se pudo","falló","timeout","sin respuesta"],
    "sad":     ["no entendí","no pude","desconectado"],
    "confused":["no entendí","no comprendo","ambiguo"],
}

def detect_emotion(text: str) -> str:
    tl = text.lower()
    if any(k in tl for k in EMOTION_KEYWORDS["error"]):    return "error"
    if any(k in tl for k in EMOTION_KEYWORDS["sad"]):      return "sad"
    if any(k in tl for k in EMOTION_KEYWORDS["confused"]): return "confused"
    if any(k in tl for k in EMOTION_KEYWORDS["happy"]):    return "happy"
    return "normal"

def get_face_info(state: str) -> tuple:
    entry = FACE_FILES.get(state, FACE_FILES["normal"])
    filename, kind = entry
    path = FACE_DIR / filename
    if path.exists(): return str(path), kind
    if kind == "video" and state in FACE_FALLBACK_IMAGE:
        fallback = FACE_DIR / FACE_FALLBACK_IMAGE[state]
        if fallback.exists(): return str(fallback), "image"
    return None, "image"


class LuneFaceWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(196, 260)
        self.setStyleSheet("QFrame { background: transparent; border: none; }")
        self._current_state = "normal"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setScaledContents(False)
        self.image_label.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(self.image_label)

        if _MULTIMEDIA_OK:
            self.video_widget = QVideoWidget()
            self.video_widget.setFixedSize(190, 250)
            self.video_widget.setStyleSheet("background: transparent; border: none;")
            self.video_widget.hide()
            layout.addWidget(self.video_widget)
            self._player = QMediaPlayer()
            self._audio  = QAudioOutput()
            self._audio.setVolume(0)
            self._player.setAudioOutput(self._audio)
            self._player.setVideoOutput(self.video_widget)
            self._player.mediaStatusChanged.connect(self._on_media_status)
        else:
            self.video_widget = None
            self._player = None

        self._fallback_label = QLabel("🤖")
        self._fallback_label.setFont(QFont("Segoe UI Emoji", 64))
        self._fallback_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fallback_label.setStyleSheet("background: transparent; border: none;")
        self._fallback_label.hide()
        layout.addWidget(self._fallback_label)

        self._revert_timer = QTimer(self)
        self._revert_timer.setSingleShot(True)
        self._revert_timer.timeout.connect(lambda: self.set_state("normal"))
        self._load_face("normal")

    def _on_media_status(self, status):
        if self._player and status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._player.setPosition(0)
            self._player.play()

    def _stop_video(self):
        if self._player: self._player.stop()
        if self.video_widget: self.video_widget.hide()

    def _load_face(self, state: str):
        path, kind = get_face_info(state)
        self._stop_video()
        if path and kind == "video" and _MULTIMEDIA_OK and self._player:
            self.image_label.hide()
            self._fallback_label.hide()
            self.video_widget.show()
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()
            return
        if path and kind == "image":
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(190, 250,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self.image_label.setPixmap(scaled)
                self.image_label.show()
                self._fallback_label.hide()
                return
        fallback_emojis = {
            "normal":"🤖","happy":"😊","thinking":"🤔","typing":"⌨️",
            "reading":"📖","sad":"😔","confused":"😕","error":"❌"
        }
        self._fallback_label.setText(fallback_emojis.get(state, "🤖"))
        self._fallback_label.show()
        self.image_label.hide()

    def set_state(self, state: str, auto_revert_ms: int = 0):
        if state == self._current_state: return
        self._current_state = state
        self._load_face(state)
        if auto_revert_ms > 0:
            self._revert_timer.start(auto_revert_ms)
        else:
            self._revert_timer.stop()


# ─── Voz ──────────────────────────────────────────────────────────────────────
class VoiceEngine:
    def __init__(self):
        self._enabled = False
        self._lock = threading.Lock()
        self._engine = None
        self._init_engine()

    def _init_engine(self):
        try:
            import edge_tts, pygame
            pygame.mixer.init()
            self._engine = "edge"
            return
        except ImportError:
            pass
        try:
            from gtts import gTTS
            import pygame
            pygame.mixer.init()
            self._engine = "gtts"
            return
        except ImportError:
            pass
        self._engine = None

    def speak(self, text: str):
        if not self._enabled or not self._engine: return
        clean = re.sub(r'[^\w\s,.!?áéíóúüñ¿¡`]', '', text, flags=re.UNICODE).strip()[:400]
        if clean:
            threading.Thread(target=self._speak_blocking, args=(clean,), daemon=True).start()

    def _speak_blocking(self, text: str):
        with self._lock:
            if self._engine == "edge":   self._speak_edge(text)
            elif self._engine == "gtts": self._speak_gtts(text)

    def _speak_edge(self, text: str):
        try:
            import asyncio, edge_tts, pygame, tempfile
            async def _synth():
                c = edge_tts.Communicate(text, voice="es-MX-DaliaNeural")
                t = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                t.close()
                await c.save(t.name)
                return t.name
            path = asyncio.run(_synth())
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy(): threading.Event().wait(0.1)
            os.unlink(path)
        except Exception: pass

    def _speak_gtts(self, text: str):
        try:
            from gtts import gTTS
            import pygame
            tts = gTTS(text, lang="es")
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            fp.seek(0)
            pygame.mixer.music.load(fp)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy(): threading.Event().wait(0.1)
        except Exception: pass

    def toggle(self) -> bool:
        self._enabled = not self._enabled
        return self._enabled

    @property
    def available(self): return self._engine is not None
    @property
    def engine_name(self): return self._engine or "sin voz"


# ─── Widgets de chat ──────────────────────────────────────────────────────────
class ProviderTab(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, provider_id, meta, parent=None):
        super().__init__(parent)
        self.provider_id = provider_id
        self.meta = meta
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(64)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)
        self.icon_lbl = QLabel(self.meta["icon"])
        self.icon_lbl.setFont(QFont("Segoe UI Emoji", 18))
        self.icon_lbl.setFixedWidth(30)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self.name_lbl = QLabel(self.meta["label"])
        self.name_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.desc_lbl = QLabel(self.meta["desc"])
        self.desc_lbl.setFont(QFont("Segoe UI", 9))
        text_col.addWidget(self.name_lbl)
        text_col.addWidget(self.desc_lbl)
        layout.addWidget(self.icon_lbl)
        layout.addLayout(text_col, 1)
        self._apply_style(False)

    def _apply_style(self, active):
        c, d = self.meta["color"], self.meta["dark"]
        if active:
            self.setStyleSheet(
                f"ProviderTab {{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                f"stop:0 {d}88,stop:1 {d}22);border-left:3px solid {c};border-radius:10px;}}"
            )
            self.name_lbl.setStyleSheet(f"color:{c};background:transparent;")
            self.desc_lbl.setStyleSheet(f"color:{c}aa;background:transparent;")
        else:
            self.setStyleSheet(
                f"ProviderTab {{background:transparent;border-left:3px solid transparent;"
                f"border-radius:10px;}}ProviderTab:hover{{background:{COLORS['surface2']};}}"
            )
            self.name_lbl.setStyleSheet(f"color:{COLORS['text']};background:transparent;")
            self.desc_lbl.setStyleSheet(f"color:{COLORS['text_muted']};background:transparent;")
        self.icon_lbl.setStyleSheet("background:transparent;")

    def set_active(self, active):
        self._active = active
        self._apply_style(active)

    def mousePressEvent(self, event):
        self.clicked.emit(self.provider_id)


class MessageBubble(QFrame):
    def __init__(self, text, is_user, provider_id="tiny_nlu", parent=None):
        super().__init__(parent)
        self.is_user = is_user
        self.provider_id = provider_id
        self._build(text)

    def _build(self, text):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 4, 12, 4)
        outer.setSpacing(10)
        meta  = PROVIDER_META.get(self.provider_id, PROVIDER_META["tiny_nlu"])
        color = meta["color"]

        if self.is_user:
            outer.addStretch()
            bubble = QFrame()
            bubble.setStyleSheet(
                f"QFrame{{background:{COLORS['user_bubble']};border-radius:16px;"
                f"border-bottom-right-radius:4px;border:1px solid {COLORS['border2']};}}"
            )
            bl = QVBoxLayout(bubble)
            bl.setContentsMargins(14, 10, 14, 10)
            bl.setSpacing(4)
            self.text_lbl = QLabel(text)
            self.text_lbl.setWordWrap(True)
            self.text_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.text_lbl.setFont(QFont("Segoe UI", 11))
            self.text_lbl.setStyleSheet(f"color:{COLORS['text']};background:transparent;")
            self.text_lbl.setMaximumWidth(520)
            bl.addWidget(self.text_lbl)
            ts = QLabel(datetime.now().strftime("%H:%M"))
            ts.setFont(QFont("Segoe UI", 8))
            ts.setStyleSheet(f"color:{COLORS['text_dim']};background:transparent;")
            ts.setAlignment(Qt.AlignmentFlag.AlignRight)
            bl.addWidget(ts)
            outer.addWidget(bubble)
        else:
            avatar = QLabel(meta["icon"])
            avatar.setFixedSize(36, 36)
            avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            avatar.setFont(QFont("Segoe UI Emoji", 15))
            avatar.setStyleSheet(
                f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                f"stop:0 {meta['dark']},stop:1 {color}44);"
                f"border-radius:10px;border:1px solid {color}55;"
            )
            outer.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
            bubble = QFrame()
            bubble.setStyleSheet(
                f"QFrame{{background:{COLORS['bot_bubble']};border-radius:16px;"
                f"border-top-left-radius:4px;border:1px solid {COLORS['border']};}}"
            )
            bl = QVBoxLayout(bubble)
            bl.setContentsMargins(14, 10, 14, 10)
            bl.setSpacing(4)
            sender = QLabel(meta["label"])
            sender.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            sender.setStyleSheet(f"color:{color};background:transparent;")
            bl.addWidget(sender)
            self.text_lbl = QLabel(text)
            self.text_lbl.setWordWrap(True)
            self.text_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.text_lbl.setFont(QFont("Segoe UI", 11))
            self.text_lbl.setStyleSheet(f"color:{COLORS['text']};background:transparent;")
            self.text_lbl.setMaximumWidth(520)
            bl.addWidget(self.text_lbl)
            ts = QLabel(datetime.now().strftime("%H:%M"))
            ts.setFont(QFont("Segoe UI", 8))
            ts.setStyleSheet(f"color:{COLORS['text_dim']};background:transparent;")
            bl.addWidget(ts)
            outer.addWidget(bubble)
            outer.addStretch()

    def update_text(self, text):
        self.text_lbl.setText(text)


class TypingIndicator(QFrame):
    def __init__(self, provider_id="tiny_nlu", parent=None):
        super().__init__(parent)
        meta = PROVIDER_META.get(provider_id, PROVIDER_META["tiny_nlu"])
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(10)
        avatar = QLabel(meta["icon"])
        avatar.setFixedSize(36, 36)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFont(QFont("Segoe UI Emoji", 15))
        avatar.setStyleSheet(
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f"stop:0 {meta['dark']},stop:1 {meta['color']}44);"
            f"border-radius:10px;border:1px solid {meta['color']}55;"
        )
        layout.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)
        dots_frame = QFrame()
        dots_frame.setStyleSheet(
            f"QFrame{{background:{COLORS['bot_bubble']};border-radius:16px;"
            f"border-top-left-radius:4px;border:1px solid {COLORS['border']};}}"
        )
        dl = QHBoxLayout(dots_frame)
        dl.setContentsMargins(16, 12, 16, 12)
        dl.setSpacing(6)
        self.dots = []
        for _ in range(3):
            dot = QLabel("●")
            dot.setFont(QFont("Segoe UI", 9))
            dot.setStyleSheet(f"color:{COLORS['text_muted']};background:transparent;")
            dl.addWidget(dot)
            self.dots.append(dot)
        layout.addWidget(dots_frame)
        layout.addStretch()
        self._dot_idx = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(300)

    def _animate(self):
        c = COLORS["accent"]
        for i, dot in enumerate(self.dots):
            dot.setStyleSheet(
                f"color:{c if i == self._dot_idx else COLORS['text_dim']};background:transparent;"
            )
        self._dot_idx = (self._dot_idx + 1) % 3

    def stop(self):
        self._timer.stop()


# ─── AI Worker ────────────────────────────────────────────────────────────────
class AIWorker(QThread):
    token_received  = pyqtSignal(str)
    response_ready  = pyqtSignal(str)
    error_occurred  = pyqtSignal(str)

    def __init__(self, ai_manager, message: str, provider_id: str, extra_context: str = ""):
        super().__init__()
        self.ai_manager    = ai_manager
        self.message       = message
        self.provider_id   = provider_id
        self.extra_context = extra_context
        self._buffer       = ""

    def run(self):
        try:
            sys_val = PROVIDER_META[self.provider_id]["system"]
            system_prompt = sys_val() if callable(sys_val) else sys_val

            def on_token(token):
                self._buffer += token
                self.token_received.emit(self._buffer)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                response = loop.run_until_complete(
                    self.ai_manager.chat(
                        self.message, system_prompt,
                        provider=self.provider_id, on_token=on_token
                    )
                )
            finally:
                loop.close()

            self.response_ready.emit(response or "Sin respuesta")
        except Exception as e:
            log_error(f"AIWorker error: {e}")
            self.error_occurred.emit(str(e))


# ─── Panel de configuración ───────────────────────────────────────────────────
class SettingsPanel(QFrame):
    saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QFrame{background:transparent;}")
        self._load_datos()
        self._build()

    def _load_datos(self):
        try:
            with open("datos.json", "r", encoding="utf-8") as f:
                self.datos_data = json.load(f)
        except Exception:
            self.datos_data = {"bot":{"personaje_default":"Ardo"},"personajes":[{}]}

    def _build(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{border:none;background:transparent;}}"
            f"QScrollBar:vertical {{background:{COLORS['surface']};width:8px;border-radius:4px;}}"
            f"QScrollBar::handle:vertical {{background:{COLORS['border2']};border-radius:4px;}}"
        )

        content = QFrame()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 20, 40, 40)
        layout.setSpacing(25)

        title = QLabel("⚙️  Configuración")
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title.setStyleSheet(f"color:{COLORS['text']};")
        layout.addWidget(title)

        self.fields = {}

        # Sección: Personalidad
        layout.addWidget(self._section_title("🎭 Personalidad del Asistente"))
        frame_pers = self._group_frame()
        fl_pers = QVBoxLayout(frame_pers)
        fl_pers.setSpacing(15)

        personaje = self.datos_data.get("personajes", [{}])[0] if self.datos_data.get("personajes") else {}
        self._add_input(fl_pers, "bot_nombre", "Nombre del Asistente",
                        personaje.get("nombre", "Ardo"), False)
        self._add_textarea(fl_pers, "bot_saludo", "Mensaje de Bienvenida",
                           personaje.get("fraseInicial", "Hola. ¿Qué quieres controlar?"), 60)
        layout.addWidget(frame_pers)

        # Botón guardar
        save_btn = QPushButton("💾  Guardar Configuración")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        save_btn.setFixedHeight(50)
        save_btn.setStyleSheet(
            f"QPushButton{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {COLORS['accent']},stop:1 {COLORS['accent2']});"
            f"color:white;border:none;border-radius:12px;margin-top:10px;}}"
            f"QPushButton:hover{{background:{COLORS['accent2']};}}"
        )
        save_btn.clicked.connect(self._save)
        layout.addWidget(save_btn)
        layout.addStretch()

        scroll.setWidget(content)
        main_layout.addWidget(scroll)

    def _section_title(self, text):
        lbl = QLabel(text)
        lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color:{COLORS['text_muted']};margin-top:10px;")
        return lbl

    def _group_frame(self):
        f = QFrame()
        f.setStyleSheet(
            f"QFrame{{background:{COLORS['surface']};border-radius:12px;"
            f"border:1px solid {COLORS['border']};padding:15px;}}"
        )
        return f

    def _add_input(self, layout, key, label_text, default_val, is_password):
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Segoe UI", 10))
        lbl.setStyleSheet(f"color:{COLORS['text']};border:none;padding:0;")
        inp = QLineEdit()
        inp.setText(default_val)
        inp.setEchoMode(
            QLineEdit.EchoMode.Password if is_password else QLineEdit.EchoMode.Normal
        )
        inp.setStyleSheet(
            f"QLineEdit{{background:{COLORS['surface2']};border:1px solid {COLORS['border']};"
            f"border-radius:8px;padding:10px 12px;color:{COLORS['text']};}}"
            f"QLineEdit:focus{{border:1px solid {COLORS['accent']};}}"
        )
        self.fields[key] = inp
        layout.addWidget(lbl)
        layout.addWidget(inp)

    def _add_textarea(self, layout, key, label_text, default_val, height):
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Segoe UI", 10))
        lbl.setStyleSheet(f"color:{COLORS['text']};border:none;padding:0;")
        txt = QTextEdit()
        txt.setPlainText(default_val)
        txt.setFixedHeight(height)
        txt.setStyleSheet(
            f"QTextEdit{{background:{COLORS['surface2']};border:1px solid {COLORS['border']};"
            f"border-radius:8px;padding:10px;color:{COLORS['text']};font-family:'Segoe UI';}}"
            f"QTextEdit:focus{{border:1px solid {COLORS['accent']};}}"
        )
        self.fields[key] = txt
        layout.addWidget(lbl)
        layout.addWidget(txt)

    def _save(self):
        if "bot" not in self.datos_data:
            self.datos_data["bot"] = {}
        if not self.datos_data.get("personajes"):
            self.datos_data["personajes"] = [{}]

        nombre = self.fields["bot_nombre"].text().strip()
        self.datos_data["bot"]["personaje_default"] = nombre
        self.datos_data["personajes"][0]["nombre"] = nombre
        self.datos_data["personajes"][0]["fraseInicial"] = \
            self.fields["bot_saludo"].toPlainText().strip()

        try:
            with open("datos.json", "w", encoding="utf-8") as f:
                json.dump(self.datos_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error guardando datos.json: {e}")

        importlib.reload(datos)
        self.saved.emit()


# ─── Ventana principal ────────────────────────────────────────────────────────
class ArdoDesktopWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ai_manager       = AIManager()
        self.voice            = VoiceEngine()
        self.current_provider = "tiny_nlu"
        self.ai_worker        = None
        self._current_bubble  = None
        self._typing_indicator= None
        self.memoria          = MemoriaManager()
        self.tools            = ToolManager()
        self._init_ui()
        log_info("Ardo Desktop iniciado")

    # ── UI ─────────────────────────────────────────────────────────────────────
    def _init_ui(self):
        self.setWindowTitle("🤖 Ardo Desktop · NLU Local")
        self.setGeometry(80, 60, 1100, 780)
        self.setMinimumSize(820, 580)
        self.setStyleSheet(f"QMainWindow,QWidget{{background:{COLORS['bg']};}}")

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_main(), 1)

    # ── Sidebar ────────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet(
            f"QFrame{{background:{COLORS['surface']};border-right:1px solid {COLORS['border']};}}"
        )
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 20, 12, 16)
        layout.setSpacing(4)

        # Logo
        logo_row = QHBoxLayout()
        icon = QLabel("🤖")
        icon.setFont(QFont("Segoe UI Emoji", 22))
        icon.setStyleSheet("background:transparent;")
        title = QVBoxLayout()
        title.setSpacing(0)
        personaje = datos.get_personaje(datos.get_bot().get("personaje_default", "Ardo"))
        self.sidebar_t1 = QLabel(personaje.get("nombre", "Ardo"))
        self.sidebar_t1.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self.sidebar_t1.setStyleSheet(f"color:{COLORS['text']};background:transparent;")
        t2 = QLabel("NLU Local · Ardo v2")
        t2.setFont(QFont("Segoe UI", 8))
        t2.setStyleSheet(f"color:{COLORS['text_muted']};background:transparent;")
        title.addWidget(self.sidebar_t1)
        title.addWidget(t2)
        logo_row.addWidget(icon)
        logo_row.addLayout(title, 1)
        layout.addLayout(logo_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background:{COLORS['border']};margin:8px 0;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        lbl = QLabel("MOTOR DE COMANDOS")
        lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color:{COLORS['text_dim']};background:transparent;padding:4px 4px 4px 6px;")
        layout.addWidget(lbl)

        self.provider_tabs = {}
        for pid, meta in PROVIDER_META.items():
            tab = ProviderTab(pid, meta)
            tab.clicked.connect(self._switch_provider)
            self.provider_tabs[pid] = tab
            layout.addWidget(tab)
        self.provider_tabs["tiny_nlu"].set_active(True)

        layout.addStretch()

        # Cara
        self.lune_face = LuneFaceWidget()
        fc = QHBoxLayout()
        fc.setContentsMargins(0, 0, 0, 0)
        fc.addStretch()
        fc.addWidget(self.lune_face)
        fc.addStretch()
        layout.addLayout(fc)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"background:{COLORS['border']};margin:4px 0;")
        sep2.setFixedHeight(1)
        layout.addWidget(sep2)

        # Botones de acción
        self._keys_btn = self._sidebar_btn("⚙️", "Configuración")
        self._keys_btn.clicked.connect(self._toggle_keys_panel)
        layout.addWidget(self._keys_btn)

        clear_btn = self._sidebar_btn("🗑", "Limpiar chat")
        clear_btn.clicked.connect(self._clear_chat)
        layout.addWidget(clear_btn)

        mem_btn = self._sidebar_btn("🧠", "Mi memoria")
        mem_btn.clicked.connect(self._show_memoria)
        layout.addWidget(mem_btn)

        tools_btn = self._sidebar_btn("🛠", "Herramientas")
        tools_btn.clicked.connect(self._show_tools)
        layout.addWidget(tools_btn)

        if self.voice.available:
            icon_voz = "🔊" if self.voice.engine_name == "edge" else "🔢"
            self._voice_btn = self._sidebar_btn(icon_voz, "Voz: OFF")
            self._voice_btn.clicked.connect(self._toggle_voice)
            layout.addWidget(self._voice_btn)

        return sidebar

    def _sidebar_btn(self, icon, label):
        btn = QPushButton(f"  {icon}  {label}")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFont(QFont("Segoe UI", 10))
        btn.setFixedHeight(38)
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{COLORS['text_muted']};"
            f"border:none;border-radius:8px;text-align:left;padding-left:8px;}}"
            f"QPushButton:hover{{background:{COLORS['surface2']};color:{COLORS['text']};}}"
        )
        return btn

    def _show_memoria(self):
        texto = self.memoria._cmd_listar()
        bubble = MessageBubble(texto, is_user=False, provider_id=self.current_provider)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self.lune_face.set_state("reading", auto_revert_ms=5000)
        self._scroll_bottom()

    def _show_tools(self):
        texto = self.tools.listar_disponibles()
        bubble = MessageBubble(texto, is_user=False, provider_id=self.current_provider)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self.lune_face.set_state("reading", auto_revert_ms=5000)
        self._scroll_bottom()

    # ── Main area ──────────────────────────────────────────────────────────────
    def _build_main(self):
        main = QFrame()
        main.setStyleSheet(f"QFrame{{background:{COLORS['bg']};border:none;}}")
        layout = QVBoxLayout(main)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget{background:transparent;}")
        self.stack.addWidget(self._build_chat_page())
        self.stack.addWidget(self._build_settings_page())
        layout.addWidget(self.stack, 1)
        layout.addWidget(self._build_input_bar())
        return main

    def _build_topbar(self):
        bar = QFrame()
        bar.setFixedHeight(56)
        bar.setStyleSheet(
            f"QFrame{{background:{COLORS['surface']};border-bottom:1px solid {COLORS['border']};}}"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        meta = PROVIDER_META[self.current_provider]
        self.topbar_icon  = QLabel(meta["icon"])
        self.topbar_icon.setFont(QFont("Segoe UI Emoji", 18))
        self.topbar_icon.setStyleSheet("background:transparent;")
        self.topbar_title = QLabel(meta["label"])
        self.topbar_title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self.topbar_title.setStyleSheet(f"color:{meta['color']};background:transparent;")
        self.topbar_desc  = QLabel("·  " + meta["desc"])
        self.topbar_desc.setFont(QFont("Segoe UI", 10))
        self.topbar_desc.setStyleSheet(f"color:{COLORS['text_muted']};background:transparent;")
        layout.addWidget(self.topbar_icon)
        layout.addSpacing(8)
        layout.addWidget(self.topbar_title)
        layout.addWidget(self.topbar_desc)
        layout.addStretch()
        self.status_dot   = QLabel("●")
        self.status_dot.setFont(QFont("Segoe UI", 10))
        self.status_dot.setStyleSheet(f"color:{COLORS['success']};background:transparent;")
        self.status_label = QLabel("Listo")
        self.status_label.setFont(QFont("Segoe UI", 9))
        self.status_label.setStyleSheet(f"color:{COLORS['text_muted']};background:transparent;")
        layout.addWidget(self.status_dot)
        layout.addSpacing(4)
        layout.addWidget(self.status_label)
        return bar

    def _build_chat_page(self):
        page = QFrame()
        page.setStyleSheet("QFrame{background:transparent;}")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:transparent;}}"
            f"QScrollBar:vertical{{border:none;background:{COLORS['surface']};width:6px;border-radius:3px;}}"
            f"QScrollBar::handle:vertical{{background:{COLORS['scrollbar']};border-radius:3px;min-height:20px;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
        )
        self.scroll.setWidgetResizable(True)
        self.chat_container = QFrame()
        self.chat_container.setStyleSheet("QFrame{background:transparent;}")
        self.messages_layout = QVBoxLayout(self.chat_container)
        self.messages_layout.setContentsMargins(0, 16, 0, 16)
        self.messages_layout.setSpacing(6)
        self.messages_layout.addStretch()
        self.scroll.setWidget(self.chat_container)
        layout.addWidget(self.scroll)
        self._add_welcome()
        return page

    def _build_settings_page(self):
        page = QFrame()
        page.setStyleSheet("QFrame{background:transparent;}")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        self.settings_panel = SettingsPanel()
        self.settings_panel.saved.connect(self._on_settings_saved)
        layout.addWidget(self.settings_panel)
        return page

    def _build_input_bar(self):
        bar = QFrame()
        bar.setFixedHeight(76)
        bar.setStyleSheet(
            f"QFrame{{background:{COLORS['surface']};border-top:1px solid {COLORS['border']};}}"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(12)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Escribe un comando… ej: enciende la luz del cuarto")
        self.input_field.setFont(QFont("Segoe UI", 11))
        self.input_field.setFixedHeight(44)
        self.input_field.setStyleSheet(
            f"QLineEdit{{background:{COLORS['surface2']};border:1px solid {COLORS['border2']};"
            f"border-radius:22px;padding:0 18px;color:{COLORS['text']};}}"
            f"QLineEdit:focus{{border:1px solid {COLORS['accent']};background:{COLORS['surface3']};}}"
        )
        self.input_field.returnPressed.connect(self._send_message)

        self.send_btn = QPushButton("➤")
        self.send_btn.setFixedSize(44, 44)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        self._update_send_btn_color()
        self.send_btn.clicked.connect(self._send_message)

        self.stop_btn = QPushButton("⏹")
        self.stop_btn.setFixedSize(44, 44)
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setFont(QFont("Segoe UI", 16))
        self.stop_btn.setStyleSheet(
            f"QPushButton{{background:{COLORS['error']};color:white;border:none;border-radius:22px;}}"
            f"QPushButton:hover{{background:#ff4d4d;}}"
        )
        self.stop_btn.clicked.connect(self._stop_generation)
        self.stop_btn.hide()

        layout.addWidget(self.input_field, 1)
        layout.addWidget(self.send_btn)
        layout.addWidget(self.stop_btn)
        return bar

    def _add_welcome(self):
        welcome = QFrame()
        welcome.setStyleSheet("QFrame{background:transparent;}")
        wl = QVBoxLayout(welcome)
        wl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.setSpacing(8)
        icon = QLabel("🤖")
        icon.setFont(QFont("Segoe UI Emoji", 40))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("background:transparent;")
        personaje = datos.get_personaje(datos.get_bot().get("personaje_default", "Ardo"))
        self.welcome_t1 = QLabel(personaje.get("nombre", "Ardo"))
        self.welcome_t1.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        self.welcome_t1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome_t1.setStyleSheet(f"color:{COLORS['text']};background:transparent;")
        saludo = personaje.get("fraseInicial", "Hola. ¿Qué quieres controlar?")
        self.welcome_t2 = QLabel(saludo)
        self.welcome_t2.setFont(QFont("Segoe UI", 11))
        self.welcome_t2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome_t2.setStyleSheet(f"color:{COLORS['text_muted']};background:transparent;")
        sub = QLabel("Motor: TinyNLU · ESP32-S3 Ardo v2 · 100% offline")
        sub.setFont(QFont("Segoe UI", 9))
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color:{COLORS['accent']};background:transparent;")
        wl.addStretch()
        wl.addWidget(icon)
        wl.addWidget(self.welcome_t1)
        wl.addWidget(self.welcome_t2)
        wl.addWidget(sub)
        wl.addStretch()
        self.messages_layout.insertWidget(0, welcome)

    # ── Lógica de envío ────────────────────────────────────────────────────────
    def _switch_provider(self, provider_id):
        if provider_id == self.current_provider: return
        self.current_provider = provider_id
        for pid, tab in self.provider_tabs.items():
            tab.set_active(pid == provider_id)
        meta = PROVIDER_META[provider_id]
        self.topbar_icon.setText(meta["icon"])
        self.topbar_title.setText(meta["label"])
        self.topbar_title.setStyleSheet(f"color:{meta['color']};background:transparent;")
        self.topbar_desc.setText("·  " + meta["desc"])
        self._update_send_btn_color()
        self.stack.setCurrentIndex(0)

    def _update_send_btn_color(self):
        c = PROVIDER_META[self.current_provider]["color"]
        d = PROVIDER_META[self.current_provider]["dark"]
        self.send_btn.setStyleSheet(
            f"QPushButton{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {d},stop:1 {c});"
            f"color:white;border:none;border-radius:22px;}}"
            f"QPushButton:hover{{background:{c};}}"
            f"QPushButton:disabled{{background:{COLORS['surface3']};color:{COLORS['text_dim']};}}"
        )

    def _stop_generation(self):
        if self.ai_worker and self.ai_worker.isRunning():
            if self.current_provider in self.ai_manager.providers:
                self.ai_manager.providers[self.current_provider].cancel_flag = True
        self._set_status("Interrumpido", COLORS["warning"])
        self.lune_face.set_state("normal")
        self.stop_btn.hide()
        self.send_btn.show()
        self.input_field.setEnabled(True)
        self.input_field.setFocus()

    def _send_message(self):
        text = self.input_field.text().strip()
        if not text: return
        self.stack.setCurrentIndex(0)

        bubble = MessageBubble(text, is_user=True, provider_id=self.current_provider)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self.input_field.clear()

        # Memoria
        respuesta_memoria = self.memoria.procesar_mensaje_usuario(text)
        if respuesta_memoria:
            bot_bubble = MessageBubble(respuesta_memoria, is_user=False,
                                       provider_id=self.current_provider)
            self.messages_layout.insertWidget(self.messages_layout.count() - 1, bot_bubble)
            self.lune_face.set_state("happy", auto_revert_ms=4000)
            self._scroll_bottom()
            return

        # Herramientas locales
        tool_result = self.tools.detectar_y_ejecutar(text)
        if tool_result:
            icono = "✅" if tool_result.ok else "❌"
            msg = f"{icono} {tool_result.mensaje}"
            bot_bubble = MessageBubble(msg, is_user=False, provider_id=self.current_provider)
            self.messages_layout.insertWidget(self.messages_layout.count() - 1, bot_bubble)
            self.lune_face.set_state("happy" if tool_result.ok else "error", auto_revert_ms=5000)
            self._scroll_bottom()
            return

        self.input_field.setEnabled(False)
        self.send_btn.hide()
        self.stop_btn.show()
        if self.current_provider in self.ai_manager.providers:
            self.ai_manager.providers[self.current_provider].cancel_flag = False

        self._set_status("Procesando…", COLORS["warning"])
        self.lune_face.set_state("thinking")

        self._typing_indicator = TypingIndicator(self.current_provider)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, self._typing_indicator)
        self._scroll_bottom()

        contexto = self.memoria.obtener_contexto_para_prompt()
        self.ai_worker = AIWorker(self.ai_manager, text, self.current_provider, contexto)
        self.ai_worker.token_received.connect(self._on_token)
        self.ai_worker.response_ready.connect(self._on_response)
        self.ai_worker.error_occurred.connect(self._on_error)
        self.ai_worker.start()

    def _on_token(self, partial):
        if self._typing_indicator and self._current_bubble is None:
            self._typing_indicator.stop()
            self._typing_indicator.deleteLater()
            self._typing_indicator = None
            self._current_bubble = MessageBubble(
                partial + " ▋", is_user=False, provider_id=self.current_provider
            )
            self.messages_layout.insertWidget(
                self.messages_layout.count() - 1, self._current_bubble
            )
            self.lune_face.set_state("typing")
        elif self._current_bubble:
            self._current_bubble.update_text(partial + " ▋")
        self._scroll_bottom()

    def _on_response(self, response):
        respuesta_limpia, acciones_ia = self.tools.parsear_respuesta_ia(response)

        if self._current_bubble:
            self._current_bubble.update_text(respuesta_limpia)
        if self._typing_indicator:
            self._typing_indicator.stop()
            self._typing_indicator.deleteLater()
            self._typing_indicator = None
        self._current_bubble = None

        self.stop_btn.hide()
        self.send_btn.show()
        self._set_status("Listo", COLORS["success"])
        self.input_field.setEnabled(True)
        self.input_field.setFocus()

        for accion in acciones_ia:
            herramienta = accion.pop("herramienta", None)
            if herramienta:
                result = self.tools.ejecutar(herramienta, **accion)
                tool_bubble = MessageBubble(
                    f"{'✅' if result.ok else '❌'} {result.mensaje}",
                    is_user=False, provider_id=self.current_provider
                )
                self.messages_layout.insertWidget(
                    self.messages_layout.count() - 1, tool_bubble
                )

        self.memoria.procesar_respuesta_lune(respuesta_limpia)
        emotion = detect_emotion(respuesta_limpia)
        self.lune_face.set_state(emotion, auto_revert_ms=6000)
        self.voice.speak(respuesta_limpia)
        self._scroll_bottom()

    def _on_error(self, error):
        if self._typing_indicator:
            self._typing_indicator.stop()
            self._typing_indicator.deleteLater()
            self._typing_indicator = None
        if self._current_bubble:
            self._current_bubble.update_text(f"❌ {error}")
        else:
            err_bubble = MessageBubble(
                f"❌ {error}", is_user=False, provider_id=self.current_provider
            )
            self.messages_layout.insertWidget(self.messages_layout.count() - 1, err_bubble)
        self._current_bubble = None
        self.stop_btn.hide()
        self.send_btn.show()
        self._set_status("Error", COLORS["error"])
        self.input_field.setEnabled(True)
        self.input_field.setFocus()
        self.lune_face.set_state("error", auto_revert_ms=8000)
        self._scroll_bottom()

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _scroll_bottom(self):
        QTimer.singleShot(60, lambda:
            self.scroll.verticalScrollBar().setValue(
                self.scroll.verticalScrollBar().maximum()
            )
        )

    def _set_status(self, text, color):
        self.status_label.setText(text)
        self.status_dot.setStyleSheet(f"color:{color};background:transparent;")

    def _toggle_voice(self):
        enabled = self.voice.toggle()
        icon_voz = "🔊" if self.voice.engine_name == "edge" else "🔢"
        self._voice_btn.setText(f"  {icon_voz}  Voz: {'ON' if enabled else 'OFF'}")

    def _toggle_keys_panel(self):
        self.stack.setCurrentIndex(1 if self.stack.currentIndex() == 0 else 0)

    def _on_settings_saved(self):
        self.ai_manager.reload_provider()
        self.stack.setCurrentIndex(0)
        personaje = datos.get_personaje(datos.get_bot().get("personaje_default", "Ardo"))
        self.sidebar_t1.setText(personaje.get("nombre", "Ardo"))
        if hasattr(self, "welcome_t1"):
            self.welcome_t1.setText(personaje.get("nombre", "Ardo"))
            self.welcome_t2.setText(personaje.get("fraseInicial", "¿Qué quieres controlar?"))
        QMessageBox.information(self, "✅ Guardado", "Configuración guardada.")

    def _clear_chat(self):
        reply = QMessageBox.question(
            self, "Limpiar chat", "¿Eliminar todos los mensajes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            while self.messages_layout.count() > 1:
                item = self.messages_layout.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            self.ai_manager.clear_history()
            self._add_welcome()
            self.lune_face.set_state("normal")

    def closeEvent(self, event):
        if hasattr(self, "memoria"):
            stats   = self.memoria.get_stats()
            resumen = (f"Sesión del {datetime.now().strftime('%d/%m/%Y')}. "
                       f"Mensajes intercambiados hoy: {stats.get('total_mensajes', 0)}.")
            self.memoria.cerrar_sesion(resumen)
        if hasattr(self, "lune_face") and self.lune_face._player:
            self.lune_face._player.stop()
        event.accept()


# ─── Entry point ──────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Ardo Desktop")

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ArdoDesktop.v1")
    except Exception:
        pass

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,     QColor(COLORS["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Base,       QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.Text,       QColor(COLORS["text"]))
    app.setPalette(palette)

    window = ArdoDesktopWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
