/**
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║  ESP2 — "Cerebro + Motor"  |  Ardo v2  |  ESP32-S3                    ║
 * ║  ────────────────────────────────────────────────────────────────────   ║
 * ║  UART←ESP1 "CMD:<texto>\n" → TinyNLU → JSON → actuadores (simulados)  ║
 * ║  UART→ESP1 "RESP:<texto>\n" para TTS                                  ║
 * ║                                                                          ║
 * ║  Hardware:                                                               ║
 * ║    UART←ESP1: RX=GPIO16  TX=GPIO17                                      ║
 * ║    LED WS2812: GPIO48 (onboard)                                         ║
 * ║    Actuadores simulados (LEDs):                                          ║
 * ║      GPIO1  = Luz principal     GPIO2  = Luz cuarto                    ║
 * ║      GPIO3  = Puerta principal  GPIO4  = Puerta trasera                ║
 * ║      GPIO5  = Robot activo      GPIO6  = Emergencia                    ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 */

#include <stdio.h>
#include <string.h>
#include <ctype.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/timers.h"

#include "esp_log.h"
#include "esp_heap_caps.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "driver/usb_serial_jtag.h"
#include "led_strip.h"

#include "tiny_nlu.h"

static const char* TAG = "ARDO2";

// ─── Pines ────────────────────────────────────────────────────────────────────
// UART1 = ESP1 (producción), UART0 = consola USB (pruebas)
#define UART_PORT     UART_NUM_1
#define UART_RX_PIN   GPIO_NUM_16   // ← conectar al TX de ESP1
#define UART_TX_PIN   GPIO_NUM_17   // ← conectar al RX de ESP1
#define UART_CONSOLE  UART_NUM_0    // ← monitor USB (ttyACM0)
#define LED_PIN       48

// Actuadores simulados: LEDs que representan estados de dispositivos
#define PIN_LIGHT_MAIN   GPIO_NUM_1
#define PIN_LIGHT_BED    GPIO_NUM_2
#define PIN_DOOR_MAIN    GPIO_NUM_3
#define PIN_DOOR_BACK    GPIO_NUM_4
#define PIN_ROBOT        GPIO_NUM_5
#define PIN_EMERGENCY    GPIO_NUM_6

// ─── Config UART ──────────────────────────────────────────────────────────────
#define UART_BAUD       115200
#define UART_BUF_SIZE   512
#define UART_CMD_PREFIX "CMD:"
#define UART_RESP_PREFIX "RESP:"

// ─── Estado de Actuadores (in-memory) ────────────────────────────────────────
typedef struct {
    bool light_main;
    bool light_bedroom;
    bool light_kitchen;
    bool door_main_open;
    bool door_back_open;
    bool robot_running;
    bool tv_on;
    bool curtain_open;
    bool emergency_active;
} home_state_t;

static home_state_t s_home = {};

// ─── RGB LED ──────────────────────────────────────────────────────────────────
static led_strip_handle_t s_led = nullptr;

static void led_set(uint8_t r, uint8_t g, uint8_t b) {
    if (!s_led) return;
    led_strip_set_pixel(s_led, 0, r, g, b);
    led_strip_refresh(s_led);
}

static void init_led() {
    led_strip_config_t cfg    = {};
    cfg.strip_gpio_num        = LED_PIN;
    cfg.max_leds              = 1;
    led_strip_rmt_config_t rm = {};
    rm.resolution_hz          = 10000000;
    esp_err_t err = led_strip_new_rmt_device(&cfg, &rm, &s_led);
    if (err != ESP_OK || s_led == nullptr) {
        ESP_LOGW(TAG, "LED strip init falló (err=0x%x) — continuando sin LED", err);
        s_led = nullptr;
        return;
    }
    led_strip_clear(s_led);
    led_set(10, 0, 10);  // magenta = esperando
}

// ─── Init GPIO de Actuadores ─────────────────────────────────────────────────
static void init_actuator_pins() {
    const gpio_num_t pins[] = {
        PIN_LIGHT_MAIN, PIN_LIGHT_BED, PIN_DOOR_MAIN,
        PIN_DOOR_BACK,  PIN_ROBOT,     PIN_EMERGENCY
    };
    gpio_config_t io_conf = {
        .pin_bit_mask = 0,
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    for (gpio_num_t p : pins) {
        io_conf.pin_bit_mask = (1ULL << p);
        gpio_config(&io_conf);
        gpio_set_level(p, 0);
    }
    ESP_LOGI(TAG, "GPIO actuadores simulados OK");
}

// ─── Init UART ────────────────────────────────────────────────────────────────
static void init_uart() {
    uart_config_t cfg = {};
    cfg.baud_rate           = UART_BAUD;
    cfg.data_bits           = UART_DATA_8_BITS;
    cfg.parity              = UART_PARITY_DISABLE;
    cfg.stop_bits           = UART_STOP_BITS_1;
    cfg.flow_ctrl           = UART_HW_FLOWCTRL_DISABLE;
    cfg.rx_flow_ctrl_thresh = 122;
    cfg.source_clk          = UART_SCLK_DEFAULT;
    uart_param_config(UART_PORT, &cfg);
    uart_set_pin(UART_PORT, UART_TX_PIN, UART_RX_PIN, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    uart_driver_install(UART_PORT, UART_BUF_SIZE, UART_BUF_SIZE, 0, nullptr, 0);
    ESP_LOGI(TAG, "UART1 OK (RX=%d TX=%d)", UART_RX_PIN, UART_TX_PIN);
}

// ─── Ejecutar Comando sobre Estado de Actuadores ─────────────────────────────
static void execute_command(const nlu_result_t* res) {
    ESP_LOGI(TAG, "EXEC intent=%s conf=%.2f",
             nlu_intent_name(res->intent), res->confidence);
    ESP_LOGI(TAG, "JSON → %s", res->json);

    switch (res->intent) {

        case INTENT_LIGHT_ON:
            if (res->target == TARGET_BEDROOM) {
                s_home.light_bedroom = true;
                gpio_set_level(PIN_LIGHT_BED, 1);
                ESP_LOGI(TAG, "[SIM] Luz cuarto → ON");
            } else {
                s_home.light_main = true;
                gpio_set_level(PIN_LIGHT_MAIN, 1);
                if (res->target == TARGET_ALL) {
                    s_home.light_bedroom = true;
                    gpio_set_level(PIN_LIGHT_BED, 1);
                    s_home.light_kitchen = true;
                }
                ESP_LOGI(TAG, "[SIM] Luz principal → ON");
            }
            led_set(255, 255, 0);  // amarillo = luz encendida
            break;

        case INTENT_LIGHT_OFF:
            if (res->target == TARGET_ALL) {
                s_home.light_main    = false;
                s_home.light_bedroom = false;
                s_home.light_kitchen = false;
                gpio_set_level(PIN_LIGHT_MAIN, 0);
                gpio_set_level(PIN_LIGHT_BED,  0);
                ESP_LOGI(TAG, "[SIM] Todas las luces → OFF");
            } else if (res->target == TARGET_BEDROOM) {
                s_home.light_bedroom = false;
                gpio_set_level(PIN_LIGHT_BED, 0);
                ESP_LOGI(TAG, "[SIM] Luz cuarto → OFF");
            } else {
                s_home.light_main = false;
                gpio_set_level(PIN_LIGHT_MAIN, 0);
                ESP_LOGI(TAG, "[SIM] Luz principal → OFF");
            }
            led_set(5, 5, 5);
            break;

        case INTENT_DOOR_OPEN: {
            gpio_num_t pin = (res->target == TARGET_BACK) ? PIN_DOOR_BACK : PIN_DOOR_MAIN;
            if (res->target == TARGET_BACK) s_home.door_back_open = true;
            else                             s_home.door_main_open = true;
            gpio_set_level(pin, 1);
            ESP_LOGI(TAG, "[SIM] Puerta %s → ABIERTA",
                     (res->target == TARGET_BACK) ? "trasera" : "principal");
            led_set(0, 255, 0);  // verde = abierta
            // Simular cierre automático en 8s
            vTaskDelay(pdMS_TO_TICKS(8000));
            gpio_set_level(pin, 0);
            if (res->target == TARGET_BACK) s_home.door_back_open = false;
            else                             s_home.door_main_open = false;
            ESP_LOGI(TAG, "[SIM] Puerta → CERRADA (automático)");
            led_set(10, 0, 10);
            break;
        }

        case INTENT_DOOR_CLOSE: {
            gpio_num_t pin = (res->target == TARGET_BACK) ? PIN_DOOR_BACK : PIN_DOOR_MAIN;
            gpio_set_level(pin, 0);
            if (res->target == TARGET_BACK) s_home.door_back_open = false;
            else                             s_home.door_main_open = false;
            ESP_LOGI(TAG, "[SIM] Puerta %s → CERRADA",
                     (res->target == TARGET_BACK) ? "trasera" : "principal");
            led_set(200, 0, 0);  // rojo = cerrada
            break;
        }

        case INTENT_ROBOT_START:
            s_home.robot_running = true;
            gpio_set_level(PIN_ROBOT, 1);
            ESP_LOGI(TAG, "[SIM] Robot → EN MARCHA");
            led_set(0, 0, 255);  // azul = robot activo
            break;

        case INTENT_ROBOT_STOP:
            s_home.robot_running = false;
            gpio_set_level(PIN_ROBOT, 0);
            ESP_LOGI(TAG, "[SIM] Robot → DETENIDO");
            led_set(10, 0, 10);
            break;

        case INTENT_EMERGENCY:
            s_home.emergency_active = true;
            gpio_set_level(PIN_EMERGENCY, 1);
            ESP_LOGW(TAG, "!!! EMERGENCIA ACTIVADA !!!");
            // Parpadeo rápido del LED de emergencia (no bloqueante en tarea propia)
            for (int i = 0; i < 10; i++) {
                led_set(255, 0, 0); vTaskDelay(pdMS_TO_TICKS(150));
                led_set(0,   0, 0); vTaskDelay(pdMS_TO_TICKS(150));
            }
            led_set(255, 0, 0);  // rojo fijo
            break;

        case INTENT_TV_ON:
            s_home.tv_on = true;
            ESP_LOGI(TAG, "[SIM] TV → ON");
            led_set(0, 200, 200);
            break;

        case INTENT_TV_OFF:
            s_home.tv_on = false;
            ESP_LOGI(TAG, "[SIM] TV → OFF");
            led_set(10, 0, 10);
            break;

        case INTENT_CURTAIN_OPEN:
            s_home.curtain_open = true;
            ESP_LOGI(TAG, "[SIM] Cortinas → ABIERTAS");
            led_set(200, 200, 0);
            break;

        case INTENT_CURTAIN_CLOSE:
            s_home.curtain_open = false;
            ESP_LOGI(TAG, "[SIM] Cortinas → CERRADAS");
            led_set(10, 0, 10);
            break;

        default:
            ESP_LOGW(TAG, "[SIM] Intent desconocido — sin acción");
            led_set(200, 50, 0);  // naranja = no entendido
            break;
    }

    // Imprimir estado completo del hogar (para monitoreo)
    ESP_LOGI(TAG, "Estado: luz_main=%d luz_cuarto=%d puerta_pral=%d robot=%d tv=%d emergency=%d",
             s_home.light_main, s_home.light_bedroom,
             s_home.door_main_open, s_home.robot_running,
             s_home.tv_on, s_home.emergency_active);
}

// ─── Tarea Principal: UART RX → NLU → Exec → UART TX ─────────────────────────
static void nlu_task(void* arg) {
    uint8_t  rx_raw[UART_BUF_SIZE];
    char     line_buf[256] = {};
    int      line_pos = 0;

    ESP_LOGI(TAG, "NLU task lista. Esperando comandos de ESP1...");

    while (true) {
        int len = uart_read_bytes(UART_PORT, rx_raw, sizeof(rx_raw) - 1,
                                  pdMS_TO_TICKS(20));
        if (len <= 0) continue;

        // Acumular en line_buf hasta encontrar '\n'
        for (int i = 0; i < len; i++) {
            char c = (char)rx_raw[i];
            if (c == '\n' || c == '\r') {
                if (line_pos == 0) continue;
                line_buf[line_pos] = '\0';
                line_pos           = 0;

                ESP_LOGI(TAG, "UART←ESP1: '%s'", line_buf);

                // Verificar prefijo "CMD:"
                if (strncmp(line_buf, UART_CMD_PREFIX, strlen(UART_CMD_PREFIX)) != 0) {
                    ESP_LOGW(TAG, "Prefijo desconocido en UART — ignorando");
                    continue;
                }

                const char* text = line_buf + strlen(UART_CMD_PREFIX);
                led_set(0, 100, 255);  // cyan = procesando

                // ── TinyNLU ──────────────────────────────────────────────────
                nlu_result_t result = {};
                nlu_process(text, &result);

                ESP_LOGI(TAG, "NLU: intent=%s target=%d conf=%.2f",
                         nlu_intent_name(result.intent),
                         (int)result.target,
                         result.confidence);

                // ── Ejecutar en hardware simulado ─────────────────────────────
                // Para intent DOOR_OPEN, el execute bloquea 8s (sim cierre auto)
                // Se corre en task separada para no bloquear la recepción UART
                if (result.intent == INTENT_DOOR_OPEN) {
                    // Clonar resultado en heap para pasar a tarea temporal
                    nlu_result_t* res_copy =
                        (nlu_result_t*)heap_caps_malloc(sizeof(nlu_result_t), MALLOC_CAP_INTERNAL);
                    if (res_copy) {
                        memcpy(res_copy, &result, sizeof(nlu_result_t));
                        xTaskCreate(
                            [](void* r) {
                                execute_command((nlu_result_t*)r);
                                heap_caps_free(r);
                                vTaskDelete(nullptr);
                            },
                            "door_exec", 4096, res_copy, 4, nullptr);
                    }
                } else {
                    execute_command(&result);
                }

                // ── Enviar respuesta a ESP1 ───────────────────────────────────
                char resp[200];
                snprintf(resp, sizeof(resp), "%s%s\n", UART_RESP_PREFIX, result.response);
                uart_write_bytes(UART_PORT, resp, strlen(resp));
                ESP_LOGI(TAG, "UART→ESP1: '%s'", result.response);

            } else {
                if (line_pos < (int)sizeof(line_buf) - 1) {
                    line_buf[line_pos++] = c;
                }
            }
        }
    }
}

// ─── Tarea: Consola de prueba via USB Serial/JTAG ────────────────────────────
// /dev/ttyACM0 en el PC conecta al periférico USB Serial/JTAG del ESP32-S3,
// que es hardware separado de UART0. Se lee con usb_serial_jtag_read_bytes.
// Acepta texto libre O con prefijo "CMD:".
// Ejemplos: "enciende la luz"  "abre la puerta"  "mueve el robot"
static void console_task(void* arg) {
    usb_serial_jtag_driver_config_t usj_cfg = {};
    usj_cfg.rx_buffer_size = 512;
    usj_cfg.tx_buffer_size = 512;
    esp_err_t err = usb_serial_jtag_driver_install(&usj_cfg);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "USB Serial/JTAG driver ya instalado o error (0x%x)", err);
    }
    ESP_LOGI(TAG, "=== CONSOLA USB: escribe un comando + Enter ===");

    uint8_t raw[512];
    char    line[256];
    int     pos = 0;

    while (true) {
        int len = usb_serial_jtag_read_bytes(raw, sizeof(raw) - 1,
                                             pdMS_TO_TICKS(50));
        for (int i = 0; i < len; i++) {
            char c = (char)raw[i];
            if (c == '\r') continue;
            if (c == '\n' || pos >= (int)sizeof(line) - 2) {
                line[pos] = '\0';
                pos = 0;
                if (strlen(line) == 0) continue;

                // Acepta texto directo O con prefijo "CMD:"
                const char* text = (strncmp(line, UART_CMD_PREFIX,
                                            strlen(UART_CMD_PREFIX)) == 0)
                                    ? line + strlen(UART_CMD_PREFIX)
                                    : line;

                ESP_LOGI(TAG, ">>> Procesando: '%s'", text);
                led_set(0, 100, 255);

                nlu_result_t result = {};
                nlu_process(text, &result);

                ESP_LOGI(TAG, "NLU → intent=%s  target=%d  conf=%.2f",
                         nlu_intent_name(result.intent),
                         (int)result.target, result.confidence);
                ESP_LOGI(TAG, "JSON  → %s", result.json);
                ESP_LOGI(TAG, "RESP  → %s", result.response);

                execute_command(&result);
            } else {
                line[pos++] = c;
            }
        }
    }
}

// ═════════════════════════════════════════════════════════════════════════════
extern "C" void app_main() {
    // Delay para que USB Serial/JTAG tenga tiempo de enumerarse
    // antes de que aparezcan logs importantes
    vTaskDelay(pdMS_TO_TICKS(3000));

    ESP_LOGI(TAG, "=== Ardo v2 — ESP2 Cerebro+Motor | ESP32-S3 ===");
    ESP_LOGI(TAG, "RAM libre: %d bytes", heap_caps_get_free_size(MALLOC_CAP_INTERNAL));

    init_led();
    init_uart();
    init_actuator_pins();
    nlu_init();

    ESP_LOGI(TAG, "Sistema listo. Esperando comandos via UART...");
    led_set(10, 0, 10);  // magenta = idle, esperando

    // La tarea principal de NLU/ejecución corre en Core 0
    xTaskCreatePinnedToCore(nlu_task,     "nlu",     8192, nullptr, 5, nullptr, 0);
    xTaskCreatePinnedToCore(console_task, "console", 4096, nullptr, 3, nullptr, 0);

    // app_main puede terminar — FreeRTOS seguirá corriendo
}
