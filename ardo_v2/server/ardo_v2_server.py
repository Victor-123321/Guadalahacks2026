#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  Ardo v2 — Servidor ardopc                                                 ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  ┌─────────────────────────────────────────────────────────────────────┐   ║
║  │  TCP :9000  ← PCM 16kHz int16 stream desde ESP1                    │   ║
║  │              → faster-whisper STT                                   │   ║
║  │              → Home Assistant conversation API                      │   ║
║  │              → Kokoro TTS → PCM 16kHz int16                        │   ║
║  │              → TCP response al ESP1                                 │   ║
║  ├─────────────────────────────────────────────────────────────────────┤   ║
║  │  HTTP :8080  GET  /ping          → health check (ESP1 turbo mode)  │   ║
║  │              POST /api/intent    → NLU directo (debug/test)        │   ║
║  └─────────────────────────────────────────────────────────────────────┘   ║
║                                                                              ║
║  Privacidad: 100% local — HA en VM en ardopc, Kokoro en local             ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import io
import json
import logging
import struct
import time
from typing import Optional

import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ─── Intenta importar Kokoro (soporta kokoro y kokoro-onnx) ──────────────────
try:
    from kokoro import KPipeline as KokoroPipeline
    KOKORO_BACKEND = "kokoro"
except ImportError:
    try:
        from kokoro_onnx import Kokoro as KokoroOnnx
        KOKORO_BACKEND = "kokoro_onnx"
    except ImportError:
        KOKORO_BACKEND = None

# ─── Importar faster-whisper ─────────────────────────────────────────────────
try:
    from faster_whisper import WhisperModel
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False
    logging.warning("faster-whisper no disponible — STT deshabilitado")

# ─── Resample (para normalizar audio de Kokoro a 16kHz) ─────────────────────
try:
    from scipy.signal import resample_poly
    from math import gcd
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# ─── Configuración ─────────────────────────────────────────────────────────────
HA_BASE_URL   = "http://ardopc:8123"
HA_TOKEN      = "TU_HA_LONG_LIVED_TOKEN"   # ← CAMBIAR
WHISPER_MODEL = "small"                     # tiny / base / small / medium
WHISPER_LANG  = "es"
KOKORO_VOICE  = "ef_dora"                  # Voz en español de Kokoro
KOKORO_SPEED  = 1.0
KOKORO_LANG   = "e"                        # 'e' = español en KPipeline

TCP_HOST   = "0.0.0.0"
TCP_PORT   = 9000
HTTP_PORT  = 8080
SAMPLE_RATE = 16000                        # PCM rate que ESP1 envía y espera
KOKORO_RATE = 24000                        # Rate nativo de Kokoro

# ─── Tokens del protocolo ESP1↔Server ────────────────────────────────────────
TCP_HEADER = b"ARDO_AUD1"   # 9 bytes — inicio de stream
TCP_FOOTER = b"ARDO_END1"   # 9 bytes — fin de stream
TTS_HEADER = b"KOKO_AUD1"   # 9 bytes — inicio de respuesta TTS

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("ardo_server")

# ─── Modelos globales (cargados una vez al arranque) ─────────────────────────
_whisper: Optional[object] = None
_kokoro:  Optional[object] = None
_ha_client: Optional[httpx.AsyncClient] = None

# ─── FastAPI App ──────────────────────────────────────────────────────────────
app = FastAPI(title="Ardo v2 Server", version="2.0.0")

class IntentRequest(BaseModel):
    text: str
    session_id: Optional[str] = "default"

# ─── Startup ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    global _whisper, _kokoro, _ha_client

    log.info("═" * 60)
    log.info("  Ardo v2 Server — Iniciando")
    log.info("═" * 60)

    # Whisper STT
    if HAS_WHISPER:
        log.info(f"  Cargando Whisper '{WHISPER_MODEL}' (CUDA)...")
        try:
            _whisper = WhisperModel(WHISPER_MODEL, device="cuda",
                                    compute_type="float16")
            log.info("  Whisper OK ✓")
        except Exception as e:
            log.warning(f"  Whisper CUDA falló ({e}), intentando CPU...")
            try:
                _whisper = WhisperModel(WHISPER_MODEL, device="cpu",
                                        compute_type="int8")
                log.info("  Whisper CPU OK ✓")
            except Exception as e2:
                log.error(f"  Whisper no disponible: {e2}")

    # Kokoro TTS
    if KOKORO_BACKEND == "kokoro":
        log.info(f"  Cargando Kokoro (lang={KOKORO_LANG})...")
        try:
            _kokoro = KokoroPipeline(lang_code=KOKORO_LANG)
            log.info("  Kokoro (KPipeline) OK ✓")
        except Exception as e:
            log.error(f"  Kokoro KPipeline falló: {e}")
    elif KOKORO_BACKEND == "kokoro_onnx":
        log.info("  Cargando Kokoro ONNX...")
        try:
            _kokoro = KokoroOnnx("kokoro-v0_19.onnx", "voices.json")
            log.info("  Kokoro ONNX OK ✓")
        except Exception as e:
            log.error(f"  Kokoro ONNX falló: {e}")
    else:
        log.warning("  Kokoro no disponible — TTS deshabilitado")

    # Home Assistant client
    _ha_client = httpx.AsyncClient(
        base_url=HA_BASE_URL,
        headers={"Authorization": f"Bearer {HA_TOKEN}",
                 "Content-Type": "application/json"},
        timeout=httpx.Timeout(10.0)
    )
    # Verificar HA
    try:
        r = await _ha_client.get("/api/")
        if r.status_code == 200:
            log.info(f"  Home Assistant OK ✓  ({HA_BASE_URL})")
        else:
            log.warning(f"  HA respondió HTTP {r.status_code}")
    except Exception as e:
        log.warning(f"  HA no accesible: {e}")

    log.info("═" * 60)
    log.info(f"  HTTP /ping,/api/intent  → :{HTTP_PORT}")
    log.info(f"  TCP audio stream        → :{TCP_PORT}")
    log.info("═" * 60)


@app.on_event("shutdown")
async def shutdown():
    if _ha_client:
        await _ha_client.aclose()

# ─── HTTP: /ping ──────────────────────────────────────────────────────────────
@app.get("/ping")
async def ping():
    return {"status": "ok", "server": "ardo_v2", "ts_ms": int(time.time() * 1000)}


# ─── HTTP: /api/intent (debug / test directo) ─────────────────────────────────
@app.post("/api/intent")
async def api_intent(req: IntentRequest):
    if not req.text.strip():
        raise HTTPException(400, "text vacío")
    result = await process_text_to_ha(req.text)
    return JSONResponse(content=result)

# ─── STT: PCM buffer → texto ──────────────────────────────────────────────────
def pcm_bytes_to_text(pcm_bytes: bytes) -> str:
    if not _whisper:
        return ""
    try:
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        t0      = time.perf_counter()
        segs, _ = _whisper.transcribe(
            samples,
            language=WHISPER_LANG,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300}
        )
        text    = " ".join(s.text.strip() for s in segs).strip()
        ms      = (time.perf_counter() - t0) * 1000
        log.info(f"[STT] '{text}' ({ms:.0f}ms, {len(pcm_bytes)//2} samples)")
        return text
    except Exception as e:
        log.error(f"[STT] Error: {e}")
        return ""

# ─── HA: Enviar texto a conversation API ─────────────────────────────────────
async def process_text_to_ha(text: str) -> dict:
    if not _ha_client or not text:
        return {"error": "no_ha_or_empty"}
    try:
        payload = {"text": text, "language": "es"}
        r       = await _ha_client.post("/api/conversation/process",
                                         content=json.dumps(payload))
        r.raise_for_status()
        data = r.json()
        speech = (data.get("response", {})
                      .get("speech", {})
                      .get("plain", {})
                      .get("speech", ""))
        log.info(f"[HA] response: '{speech}'")
        return {"ha_response": data, "speech": speech}
    except Exception as e:
        log.error(f"[HA] Error: {e}")
        return {"error": str(e), "speech": "No pude conectar con Home Assistant"}

# ─── TTS: texto → PCM int16 @ 16kHz ─────────────────────────────────────────
def text_to_pcm16k(text: str) -> bytes:
    if not _kokoro or not text:
        return b""
    try:
        t0 = time.perf_counter()
        audio_chunks = []

        if KOKORO_BACKEND == "kokoro":
            # KPipeline devuelve (graphemes, phonemes, audio_array)
            for _, _, audio in _kokoro(text, voice=KOKORO_VOICE,
                                        speed=KOKORO_SPEED,
                                        split_pattern=r'[.!?]+'):
                audio_chunks.append(np.array(audio, dtype=np.float32))
        else:
            # kokoro-onnx
            samples, _ = _kokoro.create(text, voice=KOKORO_VOICE,
                                         speed=KOKORO_SPEED, lang="es")
            audio_chunks.append(np.array(samples, dtype=np.float32))

        if not audio_chunks:
            return b""

        audio_f32 = np.concatenate(audio_chunks)

        # Resample de KOKORO_RATE a SAMPLE_RATE (24kHz → 16kHz = 2/3)
        if KOKORO_RATE != SAMPLE_RATE and HAS_SCIPY:
            g = gcd(KOKORO_RATE, SAMPLE_RATE)
            audio_f32 = resample_poly(audio_f32,
                                       SAMPLE_RATE    // g,
                                       KOKORO_RATE    // g).astype(np.float32)
        elif KOKORO_RATE != SAMPLE_RATE:
            # Fallback sin scipy: resample lineal simple
            ratio     = SAMPLE_RATE / KOKORO_RATE
            new_len   = int(len(audio_f32) * ratio)
            indices   = np.linspace(0, len(audio_f32) - 1, new_len)
            audio_f32 = np.interp(indices, np.arange(len(audio_f32)), audio_f32).astype(np.float32)

        # Clip, normalizar y convertir a int16
        audio_f32  = np.clip(audio_f32, -1.0, 1.0)
        audio_i16  = (audio_f32 * 32767).astype(np.int16)
        pcm_bytes  = audio_i16.tobytes()

        ms = (time.perf_counter() - t0) * 1000
        log.info(f"[TTS] '{text[:60]}...' → {len(pcm_bytes)} bytes ({ms:.0f}ms)")
        return pcm_bytes
    except Exception as e:
        log.error(f"[TTS] Error: {e}")
        return b""

# ─── TCP: Manejar conexión de ESP1 ───────────────────────────────────────────
async def handle_audio_client(reader: asyncio.StreamReader,
                               writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    log.info(f"[TCP] Conexión entrante de {addr}")

    try:
        # ── Leer header "ARDO_AUD1" (9 bytes) ───────────────────────────────
        hdr = await asyncio.wait_for(reader.readexactly(9), timeout=5.0)
        if hdr != TCP_HEADER:
            log.warning(f"[TCP] Header inválido: {hdr}")
            return

        # ── Leer stream PCM hasta footer "ARDO_END1" ─────────────────────────
        pcm_buffer = bytearray()
        FOOTER_LEN = len(TCP_FOOTER)
        t0_stream  = time.perf_counter()
        timeout    = 15.0  # segundos máximos de stream

        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
            except asyncio.TimeoutError:
                log.warning("[TCP] Timeout recibiendo audio")
                break
            if not chunk:
                break

            # Detectar footer en el chunk recibido
            pcm_buffer.extend(chunk)
            idx = bytes(pcm_buffer).find(TCP_FOOTER)
            if idx != -1:
                pcm_buffer = pcm_buffer[:idx]  # Recortar footer
                break

        stream_ms = (time.perf_counter() - t0_stream) * 1000
        pcm_bytes = bytes(pcm_buffer)
        log.info(f"[TCP] Stream recibido: {len(pcm_bytes)} bytes ({stream_ms:.0f}ms)")

        if len(pcm_bytes) < 3200:  # < 0.1s de audio — muy corto
            log.warning("[TCP] Audio demasiado corto — ignorando")
            writer.close()
            return

        # ── STT ──────────────────────────────────────────────────────────────
        text = await asyncio.get_event_loop().run_in_executor(
            None, pcm_bytes_to_text, pcm_bytes)

        if not text:
            log.warning("[TCP] STT no produjo texto")
            text = "comando no reconocido"

        log.info(f"[PIPELINE] '{text}'")

        # ── Home Assistant ────────────────────────────────────────────────────
        ha_result   = await process_text_to_ha(text)
        speech_text = ha_result.get("speech", "Lo siento, no pude procesar tu solicitud")

        # ── Kokoro TTS → PCM ──────────────────────────────────────────────────
        tts_pcm = await asyncio.get_event_loop().run_in_executor(
            None, text_to_pcm16k, speech_text)

        # Si TTS falló, enviar silencio de 0.5s para no dejar a ESP1 esperando
        if not tts_pcm:
            log.warning("[TCP] TTS vacío — enviando silencio")
            tts_pcm = bytes(SAMPLE_RATE)  # 0.5s de silencio int16

        # ── Enviar respuesta a ESP1 ───────────────────────────────────────────
        # Header + longitud (little-endian uint32) + PCM
        length_bytes = struct.pack("<I", len(tts_pcm))
        writer.write(TTS_HEADER + length_bytes + tts_pcm)
        await writer.drain()
        log.info(f"[TCP] Respuesta enviada: {len(tts_pcm)} bytes PCM → {addr}")

    except asyncio.IncompleteReadError:
        log.info(f"[TCP] Conexión cerrada por {addr}")
    except Exception as e:
        log.error(f"[TCP] Error con {addr}: {e}", exc_info=True)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

# ─── TCP Server (asyncio) ─────────────────────────────────────────────────────
async def run_tcp_server():
    server = await asyncio.start_server(handle_audio_client, TCP_HOST, TCP_PORT)
    log.info(f"[TCP] Servidor escuchando en {TCP_HOST}:{TCP_PORT}")
    async with server:
        await server.serve_forever()

# ─── Entry Point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import threading

    # Lanzar TCP server en un loop de asyncio dedicado (hilo separado)
    def tcp_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_tcp_server())

    t = threading.Thread(target=tcp_thread, daemon=True)
    t.start()

    # FastAPI (HTTP) en el hilo principal
    uvicorn.run(
        "ardo_v2_server:app",
        host="0.0.0.0",
        port=HTTP_PORT,
        reload=False,
        workers=1,
        log_level="info"
    )
