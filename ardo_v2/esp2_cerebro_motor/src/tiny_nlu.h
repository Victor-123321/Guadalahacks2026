#pragma once
/**
 * tiny_nlu.h — TinyNLU para Ardo v2 ESP2
 * ─────────────────────────────────────────────────────────────────────────
 * Motor NLU ligero basado en scoring ponderado de keywords.
 * Diseñado para correr en 0–2ms en ESP32-S3 sin modelo externo.
 * API idéntica a la que tendría un modelo TFLM de intenciones,
 * permitiendo swap transparente por un clasificador real.
 */

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// ─── Intenciones soportadas ───────────────────────────────────────────────────
typedef enum {
    INTENT_LIGHT_ON       = 0,
    INTENT_LIGHT_OFF      = 1,
    INTENT_DOOR_OPEN      = 2,
    INTENT_DOOR_CLOSE     = 3,
    INTENT_ROBOT_START    = 4,
    INTENT_ROBOT_STOP     = 5,
    INTENT_EMERGENCY      = 6,
    INTENT_TV_ON          = 7,
    INTENT_TV_OFF         = 8,
    INTENT_CURTAIN_OPEN   = 9,
    INTENT_CURTAIN_CLOSE  = 10,
    INTENT_UNKNOWN        = 11,
    INTENT_COUNT          = 12
} nlu_intent_t;

// ─── Modificadores de target ──────────────────────────────────────────────────
typedef enum {
    TARGET_MAIN     = 0,
    TARGET_BEDROOM  = 1,
    TARGET_KITCHEN  = 2,
    TARGET_BACK     = 3,
    TARGET_ALL      = 4
} nlu_target_t;

// ─── Resultado de inferencia ──────────────────────────────────────────────────
typedef struct {
    nlu_intent_t  intent;
    nlu_target_t  target;
    float         confidence;    // 0.0 – 1.0
    char          json[256];     // JSON listo para actuadores
    char          response[128]; // Texto de respuesta para TTS
} nlu_result_t;

/**
 * Inicializa el motor NLU. Llamar una vez en app_main.
 */
void nlu_init(void);

/**
 * Procesa texto en español y devuelve el intent con mayor puntuación.
 * @param text   Texto de entrada (null-terminated, max 256 chars)
 * @param result Resultado: intent, target, confidence, JSON y texto respuesta
 */
void nlu_process(const char* text, nlu_result_t* result);

/**
 * Devuelve el nombre legible del intent (para logging).
 */
const char* nlu_intent_name(nlu_intent_t intent);

#ifdef __cplusplus
}
#endif
