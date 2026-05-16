# Guadalahacks 2026 — Ardo v2

> **Asistente de domótica por voz para personas con discapacidad motriz**

[![ESP32-S3](https://img.shields.io/badge/ESP32--S3-ESP--IDF-blue?logo=espressif)](https://docs.espressif.com/projects/esp-idf/)
[![Python](https://img.shields.io/badge/Python-3.10+-green?logo=python)](https://python.org)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024-41BDF5?logo=homeassistant)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## ¿Qué es Ardo?

Ardo es un asistente de voz de código abierto diseñado para usuarios en silla de ruedas y personas con movilidad reducida. Escucha la wake-word **"Hey Ardo"**, procesa el comando de voz y controla dispositivos del hogar — luces, puertas, robot aspiradora — sin necesidad de manos.

---

## Arquitectura

```
  ┌─────────────────────────────────────────────────┐
  │                  PC  (ardopc)                   │
  │  faster-whisper ──► Home Assistant ──► Kokoro   │
  │  FastAPI :8080          :8123          TTS       │
  └───────────────┬─────────────────────────────────┘
                  │ TCP :9000 (audio stream)
                  │ HTTP /ping  (turbo check)
                  │
  ┌───────────────▼──────────┐   UART   ┌───────────────────────┐
  │  ESP1 — Oido + Boca      │ ◄──────► │  ESP2 — Cerebro+Motor │
  │  ESP32-S3                │          │  ESP32-S3             │
  │  · INMP441 (I2S mic)     │          │  · TinyNLU 12 intents │
  │  · MAX98357A (speaker)   │          │  · GPIO LEDs simulados│
  │  · ESP-SR AFE + VAD      │          │  · Auto door-close 8s │
  │  · Wake-word TFLM        │          └───────────────────────┘
  └──────────────────────────┘
```

### Modo Turbo (Wi-Fi disponible)
`Mic → AFE → TCP stream → faster-whisper → HA conversation API → Kokoro TTS → Speaker`

### Modo Local (sin servidor)
`Mic → Energy classifier (5 ventanas RMS) → UART → TinyNLU → GPIO simulado`

---

## Estructura del repositorio

```
ardo_v2/
├── esp1_oido_boca/          # ESP32-S3: micrófono, wake-word, Wi-Fi, altavoz
│   ├── platformio.ini
│   ├── partitions.csv
│   ├── sdkconfig.defaults
│   └── src/
│       ├── main.cpp
│       ├── CMakeLists.txt
│       ├── idf_component.yml
│       ├── hey_ardo_model.h    ← (añadir manualmente)
│       └── hey_ardo_model.cc   ← (añadir manualmente)
│
├── esp2_cerebro_motor/      # ESP32-S3: NLU + actuadores simulados
│   ├── platformio.ini
│   └── src/
│       ├── main.cpp
│       ├── tiny_nlu.h
│       ├── tiny_nlu.cpp
│       ├── CMakeLists.txt
│       └── idf_component.yml
│
└── server/
    ├── ardo_v2_server.py    # FastAPI + TCP + STT + HA + TTS
    └── requirements.txt
```

---

## Hardware

| Componente | Cant. | Notas |
|---|---|---|
| ESP32-S3-DevKitC-1 | 2 | 8 MB OPI PSRAM |
| INMP441 (micrófono I2S) | 1 | → ESP1 |
| MAX98357A + bocina | 1 | → ESP1 |
| PC con GPU (opcional) | 1 | faster-whisper + HA VM |
| LEDs + resistencias 330Ω | 6+ | actuadores simulados en ESP2 |

### Pines ESP1

| Señal | GPIO |
|---|---|
| I2S MCLK (mic) | 2 |
| I2S WS (mic) | 9 |
| I2S DATA (mic) | 13 |
| I2S BCLK (speaker) | 4 |
| I2S LRCK (speaker) | 5 |
| I2S DATA (speaker) | 6 |
| WS2812 LED | 48 |
| UART TX → ESP2 | 17 |
| UART RX ← ESP2 | 16 |

### Pines ESP2

| Señal | GPIO |
|---|---|
| UART RX ← ESP1 | 16 |
| UART TX → ESP1 | 17 |
| LED luz principal | 1 |
| LED luz recámara | 2 |
| LED puerta principal | 3 |
| LED puerta trasera | 4 |
| LED robot aspiradora | 5 |
| LED emergencia | 6 |
| WS2812 LED | 48 |

---

## Protocolo TCP (Turbo)

```
ESP1 → Servidor
  "ARDO_AUD1"  (9 bytes magic)
  <PCM int16 LE 16 kHz mono>
  "ARDO_END1"  (9 bytes magic)

Servidor → ESP1
  "KOKO_AUD1"  (9 bytes magic)
  <uint32 LE length>
  <PCM int16 LE 16 kHz mono>  ← Kokoro TTS resampled
```

## Protocolo UART (Local)

```
ESP1 → ESP2:  CMD:<texto>\n
ESP2 → ESP1:  RESP:<respuesta>\n
```

---

## Intents TinyNLU

| Intent | Target | Ejemplo |
|---|---|---|
| LIGHT_ON / LIGHT_OFF | light_main, light_bed | "enciende la luz" |
| DOOR_OPEN / DOOR_CLOSE | door_main, door_back | "abre la puerta" |
| TV_ON / TV_OFF | tv | "enciende la televisión" |
| CURTAIN_OPEN / CURTAIN_CLOSE | curtain | "abre las cortinas" |
| ROBOT_START / ROBOT_STOP | robot_vacuum | "pon a limpiar el robot" |
| THERMOSTAT | thermostat | "sube la temperatura" |
| EMERGENCY | all | "emergencia" (+2.0 boost) |

---

## Instalación del servidor

```bash
# Instalar dependencias
pip install -r ardo_v2/server/requirements.txt

# Instalar UN backend TTS
pip install kokoro          # recomendado
# pip install kokoro-onnx   # alternativa ligera

# Token de Home Assistant (perfil → Tokens de acceso de larga duración)
export HA_TOKEN="tu_token_aqui"

# Ejecutar
python ardo_v2/server/ardo_v2_server.py
# HTTP :8080  →  GET /ping
# TCP  :9000  →  stream de audio desde ESP1
```

---

## Build y flash de los firmwares

```bash
# ESP1
cd ardo_v2/esp1_oido_boca
# Editar src/main.cpp: WIFI_SSID, WIFI_PASS, SERVER_IP
pio run -t upload
pio device monitor

# ESP2
cd ardo_v2/esp2_cerebro_motor
pio run -t upload
pio device monitor
```

> **Requisito:** añadir `hey_ardo_model.h` y `hey_ardo_model.cc` (modelo de wake-word entrenado) en `esp1_oido_boca/src/` antes de compilar.

---

## Stack tecnológico

- **ESP-IDF 5.x** vía PlatformIO — firmware production-grade
- **ESP-SR AFE** — Audio Front End con VAD, AGC y supresión de ruido
- **TensorFlow Lite Micro** — inferencia de wake-word en el borde
- **faster-whisper** — STT acelerado por GPU (fallback a CPU)
- **Home Assistant** — plataforma de automatización en VM local
- **Kokoro TTS** — síntesis de voz de alta calidad
- **FastAPI + asyncio** — servidor Python con HTTP y TCP concurrentes
- **FreeRTOS** — tareas pinned a cores, EventGroups, semáforos

---

## Documentación completa

Ver [`ardo_v2/README_Ardo_v2.pdf`](ardo_v2/README_Ardo_v2.pdf) para la documentación detallada con diagramas, troubleshooting y referencia de todos los parámetros.

---

## Licencia

MIT © 2026 — Guadalahacks
