#include <stdio.h>
#include <string.h>
#include "tiny_nlu.h"

static const char* target_name(nlu_target_t t) {
    switch (t) {
        case TARGET_MAIN:    return "MAIN";
        case TARGET_BEDROOM: return "BEDROOM";
        case TARGET_KITCHEN: return "KITCHEN";
        case TARGET_BACK:    return "BACK";
        case TARGET_ALL:     return "ALL";
        default:             return "?";
    }
}

static void run(const char* text) {
    nlu_result_t r;
    nlu_process(text, &r);
    printf("\n> %s\n", text);
    printf("  Intent:     %-20s (confidence: %.2f)\n", nlu_intent_name(r.intent), r.confidence);
    printf("  Target:     %s\n", target_name(r.target));
    printf("  Response:   %s\n", r.response);
    printf("  JSON:       %s\n", r.json);
}

int main() {
    nlu_init();

    // Casos de prueba predefinidos
    const char* tests[] = {
        "enciende la luz",
        "apaga la luz del cuarto",
        "enciende todas las luces",
        "abre la puerta principal",
        "cierra la puerta trasera",
        "pon a limpiar el robot",
        "para la aspiradora",
        "enciende la televisión",
        "apaga la tele",
        "sube las cortinas",
        "cierra la cortina",
        "ayuda me caí",
        "emergencia llama una ambulancia",
        "hola buenas tardes",          // UNKNOWN
    };

    printf("═══════════════════════════════════════════════════════\n");
    printf("  TinyNLU — prueba en host\n");
    printf("═══════════════════════════════════════════════════════\n");

    for (int i = 0; i < (int)(sizeof(tests)/sizeof(tests[0])); i++)
        run(tests[i]);

    // Modo interactivo
    printf("\n═══════════════════════════════════════════════════════\n");
    printf("  Modo interactivo (escribe un comando, Ctrl+C para salir)\n");
    printf("═══════════════════════════════════════════════════════\n");

    char buf[256];
    while (1) {
        printf("\n> ");
        fflush(stdout);
        if (!fgets(buf, sizeof(buf), stdin)) break;
        // Quitar newline
        size_t len = strlen(buf);
        if (len > 0 && buf[len-1] == '\n') buf[len-1] = '\0';
        if (strlen(buf) == 0) continue;
        run(buf);
    }
    return 0;
}
