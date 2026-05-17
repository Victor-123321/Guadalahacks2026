# Guadalahacks 2026 - Ardo v2

> Asistente de domotica por voz para personas con discapacidad motriz

## Que es Ardo

Ardo es un asistente de voz de codigo abierto para controlar dispositivos IoT como luces, puertas, persianas y robot aspiradora. El proyecto combina firmware para ESP32-S3 con una app de escritorio local para pruebas y demostraciones.

## Estructura del repositorio

```text
ardo_v2/
|-- esp1_oido_boca/          # ESP32-S3: microfono, wake-word, Wi-Fi, altavoz
|-- esp2_cerebro_motor/      # ESP32-S3: NLU + actuadores simulados
`-- server/                  # Servidor FastAPI + TCP + STT + HA + TTS

main.py                      # App de escritorio local
tiny_nlu_provider.py         # NLU local de la app
tts.py                       # Voz local con Kokoro
```

## App de escritorio local

La app de escritorio en `main.py` permite probar Ardo sin hardware.

### Requisitos

- Windows
- Python 3.10, 3.11 o 3.12
- Un entorno virtual del proyecto

> Kokoro `0.9.x` no es compatible con Python 3.13.

### Instalacion rapida

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements_gui.txt
pip install "kokoro>=0.9.4" soundfile numpy pygame
```

La primera vez, Kokoro descarga sus pesos y la voz configurada en `tts.py`. Despues puede funcionar offline.

### Ejecutar

```powershell
.venv\Scripts\activate
python main.py
```

Ejemplos de comandos:

```text
enciende la luz del cuarto
apaga todas las luces
abre la cerradura
cierra las persianas
```

La app usa:

- `tiny_nlu_provider.py` para clasificar comandos locales
- `tts.py` para sintetizar voz con Kokoro
- `main.py` para la interfaz y la reproduccion de audio

### Voz local con Kokoro

`tts.py` carga Kokoro con `lang_code="e"` para espanol y usa por defecto la voz `em_alex`.

Para cambiar la voz:

```python
ArdoTTS(voice="ef_dora")
```

La salida se normaliza y se guarda a `44.1 kHz` para mejorar la compatibilidad de reproduccion en Windows.

## Intents TinyNLU

| Intent | Ejemplo |
|---|---|
| `LIGHT_ON` / `LIGHT_OFF` | `enciende la luz del cuarto` |
| `DOOR_OPEN` / `DOOR_CLOSE` | `abre la cerradura` |
| `CURTAIN_OPEN` / `CURTAIN_CLOSE` | `cierra las persianas` |
| `ROBOT_START` / `ROBOT_STOP` | `pon a limpiar el robot` |
| `TV_ON` / `TV_OFF` | `enciende la television` |
| `EMERGENCY` | `ayuda` |

## Servidor

```bash
pip install -r ardo_v2/server/requirements.txt
python ardo_v2/server/ardo_v2_server.py
```

## Build y flash de firmwares

```bash
cd ardo_v2/esp1_oido_boca
pio run -t upload

cd ../esp2_cerebro_motor
pio run -t upload
```

## Archivos locales que no se suben

El `.gitignore` excluye automaticamente:

- `.venv/`
- `__pycache__/`
- `logs/`
- `memoria.json`
- archivos de audio generados como `*.wav` y `*.mp3`
- caches locales

## Licencia

MIT - 2026 Guadalahacks
