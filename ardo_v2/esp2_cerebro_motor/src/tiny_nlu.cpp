/**
 * tiny_nlu.cpp — Motor de NLU ligero para Ardo v2
 * ─────────────────────────────────────────────────────────────────────────
 * Implementación: scoring ponderado multi-keyword sobre texto en minúsculas.
 * Latencia típica: <1ms en ESP32-S3 @240MHz.
 */

#include "tiny_nlu.h"
#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include <stdlib.h>

// ─── Regla de keyword ─────────────────────────────────────────────────────────
typedef struct {
    const char*  keyword;
    nlu_intent_t intent;
    float        weight;
} kw_rule_t;

// ─── Regla de target ──────────────────────────────────────────────────────────
typedef struct {
    const char*  keyword;
    nlu_target_t target;
} target_rule_t;

// ─── Tabla de Keywords → Intent ──────────────────────────────────────────────
// Peso 1.0 = keyword principal, 0.5 = keyword secundaria/sinónimo
static const kw_rule_t KW_TABLE[] = {
    // Luz ON
    { "enciende",   INTENT_LIGHT_ON,  1.0f }, { "encender",  INTENT_LIGHT_ON,  0.9f },
    { "prende",     INTENT_LIGHT_ON,  1.0f }, { "prender",   INTENT_LIGHT_ON,  0.9f },
    { "ilumina",    INTENT_LIGHT_ON,  0.8f }, { "luz on",    INTENT_LIGHT_ON,  1.0f },
    { "light on",   INTENT_LIGHT_ON,  1.0f },
    // Luz OFF
    { "apaga",      INTENT_LIGHT_OFF, 1.0f }, { "apagar",    INTENT_LIGHT_OFF, 0.9f },
    { "apaga la",   INTENT_LIGHT_OFF, 0.9f }, { "luz off",   INTENT_LIGHT_OFF, 1.0f },
    { "oscuro",     INTENT_LIGHT_OFF, 0.6f },
    // Puerta OPEN
    { "abre",       INTENT_DOOR_OPEN, 1.0f }, { "abrir",     INTENT_DOOR_OPEN, 0.9f },
    { "abrela",     INTENT_DOOR_OPEN, 0.9f }, { "open",      INTENT_DOOR_OPEN, 0.8f },
    { "destrab",    INTENT_DOOR_OPEN, 0.8f }, { "desbloquea",INTENT_DOOR_OPEN, 0.7f },
    // Puerta CLOSE
    { "cierra",     INTENT_DOOR_CLOSE,1.0f }, { "cerrar",    INTENT_DOOR_CLOSE,0.9f },
    { "ciérrala",   INTENT_DOOR_CLOSE,0.9f }, { "traba",     INTENT_DOOR_CLOSE,0.7f },
    { "bloquea",    INTENT_DOOR_CLOSE,0.7f }, { "close",     INTENT_DOOR_CLOSE,0.8f },
    // Robot START
    { "mueve",      INTENT_ROBOT_START,1.0f },{ "pon",       INTENT_ROBOT_START,0.7f },
    { "robot",      INTENT_ROBOT_START,0.6f },{ "aspiradora",INTENT_ROBOT_START,0.8f },
    { "limpia",     INTENT_ROBOT_START,0.9f },{ "vacuum",    INTENT_ROBOT_START,0.8f },
    // Robot STOP
    { "para el robot",  INTENT_ROBOT_STOP, 1.0f },
    { "detén el robot", INTENT_ROBOT_STOP, 1.0f },
    { "para la aspiradora", INTENT_ROBOT_STOP, 1.0f },
    { "stop robot", INTENT_ROBOT_STOP, 1.0f },
    // Emergencia
    { "ayuda",      INTENT_EMERGENCY, 1.0f }, { "auxilio",   INTENT_EMERGENCY, 1.0f },
    { "socorro",    INTENT_EMERGENCY, 1.0f }, { "sos",       INTENT_EMERGENCY, 1.0f },
    { "emergencia", INTENT_EMERGENCY, 1.0f }, { "caí",       INTENT_EMERGENCY, 0.9f },
    { "me caí",     INTENT_EMERGENCY, 1.0f }, { "dolor",     INTENT_EMERGENCY, 0.8f },
    { "accidente",  INTENT_EMERGENCY, 0.9f }, { "ambulancia",INTENT_EMERGENCY, 0.9f },
    { "llama",      INTENT_EMERGENCY, 0.5f }, // solo suma si hay otro KW emergencia
    // TV
    { "televisión", INTENT_TV_ON,     0.7f }, { "tele",      INTENT_TV_ON,     0.7f },
    { "tv",         INTENT_TV_ON,     0.7f },
    // Cortinas
    { "cortina",    INTENT_CURTAIN_OPEN, 0.6f }, { "persiana", INTENT_CURTAIN_OPEN, 0.6f },
    { "sube",       INTENT_CURTAIN_OPEN, 0.5f },
    { "baja",       INTENT_CURTAIN_CLOSE,0.5f }, { "cierra la cortina", INTENT_CURTAIN_CLOSE, 1.0f },
};
static const int KW_COUNT = (int)(sizeof(KW_TABLE) / sizeof(kw_rule_t));

// ─── Tabla de Targets ─────────────────────────────────────────────────────────
static const target_rule_t TARGET_TABLE[] = {
    { "cuarto",     TARGET_BEDROOM }, { "dormitorio",  TARGET_BEDROOM },
    { "recámara",   TARGET_BEDROOM }, { "habitación",  TARGET_BEDROOM },
    { "cocina",     TARGET_KITCHEN }, { "kitchen",     TARGET_KITCHEN },
    { "trasera",    TARGET_BACK    }, { "trasero",     TARGET_BACK    },
    { "back",       TARGET_BACK    }, { "todo",        TARGET_ALL     },
    { "todas",      TARGET_ALL     }, { "todos",       TARGET_ALL     },
};
static const int TARGET_COUNT = (int)(sizeof(TARGET_TABLE) / sizeof(target_rule_t));

// ─── Respuestas de texto por intent + target ──────────────────────────────────
typedef struct { nlu_intent_t intent; nlu_target_t target; const char* text; } resp_map_t;

static const resp_map_t RESP_TABLE[] = {
    { INTENT_LIGHT_ON,    TARGET_MAIN,    "Luz principal encendida" },
    { INTENT_LIGHT_ON,    TARGET_BEDROOM, "Luz del cuarto encendida" },
    { INTENT_LIGHT_ON,    TARGET_KITCHEN, "Luz de la cocina encendida" },
    { INTENT_LIGHT_ON,    TARGET_ALL,     "Todas las luces encendidas" },
    { INTENT_LIGHT_OFF,   TARGET_MAIN,    "Luz principal apagada" },
    { INTENT_LIGHT_OFF,   TARGET_BEDROOM, "Luz del cuarto apagada" },
    { INTENT_LIGHT_OFF,   TARGET_ALL,     "Todas las luces apagadas" },
    { INTENT_DOOR_OPEN,   TARGET_MAIN,    "Abriendo la puerta principal" },
    { INTENT_DOOR_OPEN,   TARGET_BACK,    "Abriendo la puerta trasera" },
    { INTENT_DOOR_CLOSE,  TARGET_MAIN,    "Cerrando la puerta principal" },
    { INTENT_DOOR_CLOSE,  TARGET_BACK,    "Cerrando la puerta trasera" },
    { INTENT_ROBOT_START, TARGET_MAIN,    "Robot en marcha" },
    { INTENT_ROBOT_START, TARGET_KITCHEN, "Robot dirigiéndose a la cocina" },
    { INTENT_ROBOT_START, TARGET_BEDROOM, "Robot dirigiéndose al cuarto" },
    { INTENT_ROBOT_STOP,  TARGET_MAIN,    "Robot detenido" },
    { INTENT_EMERGENCY,   TARGET_MAIN,    "Activando alerta de emergencia. Llamando ayuda." },
    { INTENT_TV_ON,       TARGET_MAIN,    "Encendiendo televisión" },
    { INTENT_TV_OFF,      TARGET_MAIN,    "Apagando televisión" },
    { INTENT_CURTAIN_OPEN,  TARGET_MAIN,  "Abriendo cortinas" },
    { INTENT_CURTAIN_CLOSE, TARGET_MAIN,  "Cerrando cortinas" },
    { INTENT_UNKNOWN,     TARGET_MAIN,    "No entendí el comando" },
};
static const int RESP_COUNT = (int)(sizeof(RESP_TABLE) / sizeof(resp_map_t));

// ─── Nombres de intents (logging) ─────────────────────────────────────────────
static const char* INTENT_NAMES[] = {
    "LIGHT_ON","LIGHT_OFF","DOOR_OPEN","DOOR_CLOSE",
    "ROBOT_START","ROBOT_STOP","EMERGENCY","TV_ON","TV_OFF",
    "CURTAIN_OPEN","CURTAIN_CLOSE","UNKNOWN"
};

static const char* TARGET_IDS[] = {
    "light_main","light_main","door_main","door_main",
    "robot_vacuum","robot_vacuum","alert_buzzer","tv_main","tv_main",
    "curtain_main","curtain_main","none"
};

static const char* INTENT_ACTIONS[] = {
    "on","off","open","close",
    "move","stop","alert","on","off",
    "open","close","noop"
};

static int INTENT_PRIORITIES[] = {
    3, 3, 2, 2,
    3, 3, 1, 3, 3,
    3, 3, 3
};

// ─── Utilidades de string ─────────────────────────────────────────────────────
static void str_lower(const char* src, char* dst, size_t max) {
    size_t i = 0;
    for (; i < max - 1 && src[i]; i++) dst[i] = (char)tolower((unsigned char)src[i]);
    dst[i] = '\0';
}

static bool str_contains(const char* haystack, const char* needle) {
    return strstr(haystack, needle) != nullptr;
}

// ─── API Pública ──────────────────────────────────────────────────────────────
void nlu_init(void) {
    // Sin estado inicial que inicializar en este motor de reglas
}

void nlu_process(const char* text, nlu_result_t* result) {
    if (!text || !result) return;

    char lower[256] = {};
    str_lower(text, lower, sizeof(lower));

    // ── Scoring por intent ────────────────────────────────────────────────────
    float scores[INTENT_COUNT] = {};
    for (int i = 0; i < KW_COUNT; i++) {
        if (str_contains(lower, KW_TABLE[i].keyword)) {
            scores[KW_TABLE[i].intent] += KW_TABLE[i].weight;
        }
    }

    // La emergencia siempre gana si tiene alguna puntuación
    if (scores[INTENT_EMERGENCY] > 0.0f)
        scores[INTENT_EMERGENCY] += 2.0f;

    // Desambiguar TV (necesita "enciende" o "apaga" + "tele/tv/televisión")
    if (scores[INTENT_TV_ON] > 0.3f) {
        if (str_contains(lower, "apaga") || str_contains(lower, "off")) {
            scores[INTENT_TV_OFF]  = scores[INTENT_TV_ON] + scores[INTENT_LIGHT_OFF];
            scores[INTENT_TV_ON]   = 0.0f;
            scores[INTENT_LIGHT_OFF] = 0.0f;
        } else if (str_contains(lower, "enciende") || str_contains(lower, "prende")) {
            scores[INTENT_TV_ON]   += 1.0f;
            scores[INTENT_LIGHT_ON] = 0.0f;
        }
    }

    // Desambiguar cortina "baja" → CLOSE, "sube" → OPEN
    if (scores[INTENT_CURTAIN_OPEN] > 0.0f || scores[INTENT_CURTAIN_CLOSE] > 0.0f) {
        if (str_contains(lower, "baja") || str_contains(lower, "cierra"))
            scores[INTENT_CURTAIN_CLOSE] += 1.0f;
        if (str_contains(lower, "sube") || str_contains(lower, "abre"))
            scores[INTENT_CURTAIN_OPEN]  += 1.0f;
    }

    // ── Seleccionar mejor intent ──────────────────────────────────────────────
    nlu_intent_t best = INTENT_UNKNOWN;
    float        best_score = 0.2f;  // umbral mínimo
    for (int i = 0; i < INTENT_COUNT; i++) {
        if (scores[i] > best_score) { best_score = scores[i]; best = (nlu_intent_t)i; }
    }

    // ── Detectar target ───────────────────────────────────────────────────────
    nlu_target_t tgt = TARGET_MAIN;
    for (int i = 0; i < TARGET_COUNT; i++) {
        if (str_contains(lower, TARGET_TABLE[i].keyword)) {
            tgt = TARGET_TABLE[i].target;
            break;
        }
    }

    result->intent     = best;
    result->target     = tgt;
    result->confidence = (best == INTENT_UNKNOWN) ? 0.0f : (best_score / 3.0f);
    if (result->confidence > 1.0f) result->confidence = 1.0f;

    // ── Buscar texto de respuesta ─────────────────────────────────────────────
    const char* resp_text = "Entendido";
    for (int i = 0; i < RESP_COUNT; i++) {
        if (RESP_TABLE[i].intent == best && RESP_TABLE[i].target == tgt) {
            resp_text = RESP_TABLE[i].text; break;
        }
        if (RESP_TABLE[i].intent == best && RESP_TABLE[i].target == TARGET_MAIN) {
            resp_text = RESP_TABLE[i].text;  // fallback a TARGET_MAIN
        }
    }
    strncpy(result->response, resp_text, sizeof(result->response) - 1);

    // ── Construir JSON de comando ─────────────────────────────────────────────
    // Determinar target string según target_id + modificador
    const char* tgt_str = TARGET_IDS[best];
    char tgt_mod[48] = {};
    if (best >= INTENT_LIGHT_ON && best <= INTENT_LIGHT_OFF) {
        if      (tgt == TARGET_BEDROOM) snprintf(tgt_mod, sizeof(tgt_mod), "light_bedroom");
        else if (tgt == TARGET_KITCHEN) snprintf(tgt_mod, sizeof(tgt_mod), "light_kitchen");
        else if (tgt == TARGET_ALL)     snprintf(tgt_mod, sizeof(tgt_mod), "light_main");
        else                            snprintf(tgt_mod, sizeof(tgt_mod), "light_main");
        tgt_str = tgt_mod;
    } else if (best == INTENT_DOOR_OPEN || best == INTENT_DOOR_CLOSE) {
        tgt_str = (tgt == TARGET_BACK) ? "door_back" : "door_main";
    } else if (best == INTENT_ROBOT_START) {
        tgt_str = "robot_vacuum";
    }

    const char* extra_params = "";
    char params_buf[64] = {};
    if (best == INTENT_DOOR_OPEN || best == INTENT_DOOR_CLOSE) {
        snprintf(params_buf, sizeof(params_buf), ",\"duration_ms\":8000");
        extra_params = params_buf;
    } else if (best == INTENT_EMERGENCY) {
        snprintf(params_buf, sizeof(params_buf), ",\"repeat\":5,\"msg\":\"EMERGENCIA\"");
        extra_params = params_buf;
    } else if (best == INTENT_ROBOT_START) {
        const char* dest = (tgt == TARGET_KITCHEN) ? "kitchen" :
                           (tgt == TARGET_BEDROOM) ? "bedroom" : "living_room";
        snprintf(params_buf, sizeof(params_buf), ",\"destination\":\"%s\"", dest);
        extra_params = params_buf;
    } else if (tgt == TARGET_ALL) {
        snprintf(params_buf, sizeof(params_buf), ",\"broadcast\":true");
        extra_params = params_buf;
    }

    static int s_cmd_id = 0;
    s_cmd_id = (s_cmd_id + 1) % 65535;

    snprintf(result->json, sizeof(result->json),
        "{\"type\":\"cmd\",\"source\":\"esp2_nlu\","
        "\"priority\":%d,\"target\":\"%s\","
        "\"action\":\"%s\",\"id\":%d,\"params\":{%s}}",
        INTENT_PRIORITIES[best], tgt_str,
        INTENT_ACTIONS[best], s_cmd_id,
        (*extra_params == ',') ? extra_params + 1 : extra_params);
}

const char* nlu_intent_name(nlu_intent_t intent) {
    if (intent >= 0 && intent < INTENT_COUNT) return INTENT_NAMES[intent];
    return "INVALID";
}
