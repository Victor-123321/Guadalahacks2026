/**
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║  ESP1 — "Oído + Boca"  |  Ardo v2  |  ESP32-S3                        ║
 * ║  ────────────────────────────────────────────────────────────────────   ║
 * ║  Wake Word → [TURBO] TCP PCM stream → PC (Whisper+HA+Kokoro) → play   ║
 * ║              [LOCAL] energy classify → UART → ESP2 TinyNLU → play     ║
 * ║                                                                          ║
 * ║  Hardware:                                                               ║
 * ║    Mic  INMP441 : BCLK=GPIO2  WS=GPIO9  DIN=GPIO13                     ║
 * ║    Spkr MAX98357: BCLK=GPIO4  WS=GPIO5  DOUT=GPIO6                     ║
 * ║    LED  WS2812  : GPIO48  (onboard ESP32-S3-DevKitC)                   ║
 * ║    UART→ESP2    : TX=GPIO17  RX=GPIO16                                  ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 */

#include <stdio.h>
#include <string.h>
#include <math.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"

#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "driver/uart.h"
#include "led_strip.h"
#include "lwip/sockets.h"
#include "lwip/netdb.h"
#include "esp_http_client.h"

#include "hey_ardo_model.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include "tensorflow/lite/micro/micro_allocator.h"
#include "tensorflow/lite/micro/micro_resource_variable.h"
#include "tensorflow/lite/experimental/microfrontend/lib/frontend.h"
#include "tensorflow/lite/experimental/microfrontend/lib/frontend_util.h"
#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"

// ─── Configuración de Usuario ─────────────────────────────────────────────────
#define WIFI_SSID           "TU_RED_WIFI"          // ← CAMBIAR
#define WIFI_PASSWORD       "TU_PASSWORD_WIFI"      // ← CAMBIAR
#define PC_HOST             "ardopc"                // hostname o IP de la PC
#define PC_PING_PORT        8080
#define PC_AUDIO_PORT       9000
#define HA_TOKEN            "TU_HA_LONG_LIVED_TOKEN" // ← CAMBIAR

// ─── Pines ────────────────────────────────────────────────────────────────────
#define MIC_BCLK            GPIO_NUM_2
#define MIC_WS              GPIO_NUM_9
#define MIC_DIN             GPIO_NUM_13
#define SPK_BCLK            GPIO_NUM_4
#define SPK_WS              GPIO_NUM_5
#define SPK_DOUT            GPIO_NUM_6
#define LED_PIN             48
#define UART_PORT           UART_NUM_1
#define UART_TX             GPIO_NUM_17
#define UART_RX             GPIO_NUM_16

// ─── Parámetros WW (igual que código original) ────────────────────────────────
#define SAMPLE_RATE          16000
#define NUM_MEL_CHANNELS     40
#define WINDOW_SIZE_MS       30
#define STEP_SIZE_MS         10
#define WARMUP_READS         50
#define SLIDING_WINDOW_SIZE  10
#define PROBABILITY_CUTOFF   0.15f
#define COOLDOWN_MS          2000
#define TENSOR_ARENA_SIZE    (96 * 1024)
#define I2S_BLOCK_SAMPLES    160
#define I2S_BLOCK_BYTES      (I2S_BLOCK_SAMPLES * sizeof(int16_t))
#define I2S_QUEUE_DEPTH      8

// ─── Parámetros de Captura Post-WW ───────────────────────────────────────────
#define CMD_CAPTURE_FRAMES   250    // ~2.5s a 10ms/frame
#define CMD_SILENCE_FRAMES   30     // 300ms de silencio → fin de utterance
#define TCP_SEND_TIMEOUT_MS  5000   // timeout envío TCP
#define TCP_RECV_TIMEOUT_MS  15000  // timeout respuesta TTS
#define UART_RESP_TIMEOUT_MS 8000   // timeout respuesta ESP2
#define PING_INTERVAL_MS     5000

// ─── TCP: Tokens de Protocolo ─────────────────────────────────────────────────
static const char TCP_HEADER[]  = "ARDO_AUD1";  // 9 bytes
static const char TCP_FOOTER[]  = "ARDO_END1";  // 9 bytes
static const char TTS_HEADER[]  = "KOKO_AUD1";  // 9 bytes

// ─── UART: Protocolo de Framing ───────────────────────────────────────────────
#define UART_CMD_PREFIX     "CMD:"
#define UART_RESP_PREFIX    "RESP:"
#define UART_BAUD           115200
#define UART_BUF_SIZE       512

// ─── State Machine ────────────────────────────────────────────────────────────
typedef enum {
    STATE_IDLE,
    STATE_WW_DETECTED,
    STATE_TURBO_STREAM,
    STATE_LOCAL_CAPTURE,
    STATE_WAITING_ESP2,
    STATE_PLAYBACK,
    STATE_COOLDOWN
} app_state_t;

// ─── Clasificador Local (6 intents, fallback sin PC) ─────────────────────────
typedef enum {
    LOCAL_INTENT_LIGHT_ON = 0,
    LOCAL_INTENT_LIGHT_OFF,
    LOCAL_INTENT_DOOR_OPEN,
    LOCAL_INTENT_DOOR_CLOSE,
    LOCAL_INTENT_EMERGENCY,
    LOCAL_INTENT_ROBOT,
    LOCAL_INTENT_UNKNOWN
} local_intent_t;

static const char* LOCAL_INTENT_CMDS[] = {
    "enciende la luz",
    "apaga la luz",
    "abre la puerta",
    "cierra la puerta",
    "ayuda emergencia",
    "mueve el robot"
};

// ─── Variables Globales ───────────────────────────────────────────────────────
static const char* TAG = "ARDO1";

static uint8_t*              s_tensor_arena   = nullptr;
static i2s_chan_handle_t     s_rx_chan         = nullptr;
static i2s_chan_handle_t     s_tx_chan         = nullptr;
static led_strip_handle_t    s_led            = nullptr;
static const esp_afe_sr_iface_t* s_afe_handle = nullptr;
static esp_afe_sr_data_t*    s_afe_data       = nullptr;
static struct FrontendConfig s_fe_config;
static struct FrontendState  s_fe_state;

static QueueHandle_t  s_i2s_queue     = nullptr;  // raw PCM blocks
static QueueHandle_t  s_speaker_queue = nullptr;  // PCM blocks to play
static EventGroupHandle_t s_evt       = nullptr;

#define EVT_WIFI_CONNECTED  BIT0
#define EVT_WW_DETECTED     BIT1
#define EVT_TCP_DONE        BIT2
#define EVT_UART_RESP       BIT3
#define EVT_SPK_DONE        BIT4

static volatile bool  s_turbo_mode    = false;
static volatile app_state_t s_state  = STATE_IDLE;
static SemaphoreHandle_t s_state_mux = nullptr;

// Shared buffer for UART response
static char  s_uart_resp_buf[256]     = {};
static SemaphoreHandle_t s_uart_mux  = nullptr;

// ─── LED Helper ───────────────────────────────────────────────────────────────
static void led_set(uint8_t r, uint8_t g, uint8_t b) {
    led_strip_set_pixel(s_led, 0, r, g, b);
    led_strip_refresh(s_led);
}

// ─── Init LED ─────────────────────────────────────────────────────────────────
static void init_led() {
    led_strip_config_t cfg    = {};
    cfg.strip_gpio_num        = LED_PIN;
    cfg.max_leds              = 1;
    led_strip_rmt_config_t rm = {};
    rm.resolution_hz          = 10000000;
    led_strip_new_rmt_device(&cfg, &rm, &s_led);
    led_strip_clear(s_led);
    led_set(10, 10, 10);
}

// ─── Init Mic I2S ─────────────────────────────────────────────────────────────
static void init_microphone() {
    i2s_chan_config_t chan_cfg  = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num       = 4;
    chan_cfg.dma_frame_num      = I2S_BLOCK_SAMPLES;
    i2s_new_channel(&chan_cfg, nullptr, &s_rx_chan);

    i2s_std_config_t std_cfg   = {};
    std_cfg.clk_cfg            = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE);
    std_cfg.slot_cfg           = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
                                     I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO);
    std_cfg.gpio_cfg.mclk      = I2S_GPIO_UNUSED;
    std_cfg.gpio_cfg.bclk      = MIC_BCLK;
    std_cfg.gpio_cfg.ws        = MIC_WS;
    std_cfg.gpio_cfg.dout      = I2S_GPIO_UNUSED;
    std_cfg.gpio_cfg.din       = MIC_DIN;
    std_cfg.slot_cfg.slot_mode = I2S_SLOT_MODE_MONO;
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

    i2s_channel_init_std_mode(s_rx_chan, &std_cfg);
    i2s_channel_enable(s_rx_chan);
    ESP_LOGI(TAG, "Mic I2S OK");
}

// ─── Init Speaker I2S ─────────────────────────────────────────────────────────
static void init_speaker() {
    i2s_chan_config_t tx_cfg  = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    tx_cfg.dma_desc_num       = 4;
    tx_cfg.dma_frame_num      = 256;
    i2s_new_channel(&tx_cfg, &s_tx_chan, nullptr);

    i2s_std_config_t std_cfg  = {};
    std_cfg.clk_cfg           = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE);
    std_cfg.slot_cfg          = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
                                    I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
    std_cfg.gpio_cfg.mclk     = I2S_GPIO_UNUSED;
    std_cfg.gpio_cfg.bclk     = SPK_BCLK;
    std_cfg.gpio_cfg.ws       = SPK_WS;
    std_cfg.gpio_cfg.dout     = SPK_DOUT;
    std_cfg.gpio_cfg.din      = I2S_GPIO_UNUSED;

    i2s_channel_init_std_mode(s_tx_chan, &std_cfg);
    i2s_channel_enable(s_tx_chan);
    ESP_LOGI(TAG, "Speaker I2S OK");
}

// ─── Init AFE ─────────────────────────────────────────────────────────────────
static void init_afe() {
    srmodel_list_t* models = esp_srmodel_init("model");
    afe_config_t* cfg      = afe_config_init("M", models, AFE_TYPE_SR, AFE_MODE_LOW_COST);
    cfg->wakenet_init      = false;
    cfg->vad_init          = true;
    cfg->agc_init          = true;
    s_afe_handle           = esp_afe_handle_from_config(cfg);
    s_afe_data             = s_afe_handle->create_from_config(cfg);
    ESP_LOGI(TAG, "AFE OK — feed=%d fetch=%d",
             s_afe_handle->get_feed_chunksize(s_afe_data),
             s_afe_handle->get_fetch_chunksize(s_afe_data));
}

// ─── Init Microfrontend ───────────────────────────────────────────────────────
static bool init_frontend() {
    memset(&s_fe_config, 0, sizeof(s_fe_config));
    s_fe_config.window.size_ms                       = WINDOW_SIZE_MS;
    s_fe_config.window.step_size_ms                  = STEP_SIZE_MS;
    s_fe_config.filterbank.num_channels              = NUM_MEL_CHANNELS;
    s_fe_config.filterbank.lower_band_limit          = 125.0f;
    s_fe_config.filterbank.upper_band_limit          = 7500.0f;
    s_fe_config.noise_reduction.smoothing_bits       = 10;
    s_fe_config.noise_reduction.even_smoothing       = 0.025f;
    s_fe_config.noise_reduction.odd_smoothing        = 0.06f;
    s_fe_config.noise_reduction.min_signal_remaining = 0.05f;
    s_fe_config.pcan_gain_control.enable_pcan        = 1;
    s_fe_config.pcan_gain_control.strength           = 0.95f;
    s_fe_config.pcan_gain_control.offset             = 80.0f;
    s_fe_config.pcan_gain_control.gain_bits          = 21;
    s_fe_config.log_scale.enable_log                 = 1;
    s_fe_config.log_scale.scale_shift                = 6;
    bool ok = FrontendPopulateState(&s_fe_config, &s_fe_state, SAMPLE_RATE);
    if (ok) ESP_LOGI(TAG, "MicFrontend OK");
    return ok;
}

// ─── Init WiFi (event-driven) ─────────────────────────────────────────────────
static void wifi_event_handler(void* arg, esp_event_base_t base,
                                int32_t id, void* data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "WiFi desconectado — reintentando...");
        xEventGroupClearBits(s_evt, EVT_WIFI_CONNECTED);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* ev = (ip_event_got_ip_t*)data;
        ESP_LOGI(TAG, "WiFi OK — IP: " IPSTR, IP2STR(&ev->ip_info.ip));
        xEventGroupSetBits(s_evt, EVT_WIFI_CONNECTED);
    }
}

static void init_wifi() {
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_cfg));

    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                         wifi_event_handler, nullptr, nullptr);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                         wifi_event_handler, nullptr, nullptr);

    wifi_config_t wifi_cfg = {};
    strncpy((char*)wifi_cfg.sta.ssid,     WIFI_SSID,     sizeof(wifi_cfg.sta.ssid));
    strncpy((char*)wifi_cfg.sta.password, WIFI_PASSWORD, sizeof(wifi_cfg.sta.password));
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "WiFi iniciado — esperando conexión...");
}

// ─── Init UART (para ESP2) ────────────────────────────────────────────────────
static void init_uart() {
    uart_config_t cfg = {
        .baud_rate           = UART_BAUD,
        .data_bits           = UART_DATA_8_BITS,
        .parity              = UART_PARITY_DISABLE,
        .stop_bits           = UART_STOP_BITS_1,
        .flow_ctrl           = UART_HW_FLOWCTRL_DISABLE,
        .rx_flow_ctrl_thresh = 122,
        .source_clk          = UART_SCLK_DEFAULT,
    };
    uart_param_config(UART_PORT, &cfg);
    uart_set_pin(UART_PORT, UART_TX, UART_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    uart_driver_install(UART_PORT, UART_BUF_SIZE, UART_BUF_SIZE, 0, nullptr, 0);
    ESP_LOGI(TAG, "UART1 OK (TX=%d RX=%d)", UART_TX, UART_RX);
}

// ─── Sliding Window (igual que original) ─────────────────────────────────────
typedef struct { float buf[SLIDING_WINDOW_SIZE]; int head, count; float sum; } SlidingWindow;
static void sw_reset(SlidingWindow* w) { memset(w, 0, sizeof(*w)); }
static float sw_push(SlidingWindow* w, float p) {
    w->sum -= w->buf[w->head];
    w->buf[w->head] = p;
    w->sum += p;
    w->head = (w->head + 1) % SLIDING_WINDOW_SIZE;
    if (w->count < SLIDING_WINDOW_SIZE) w->count++;
    return w->sum / (float)w->count;
}

// ─── TCP: Conectar al servidor ────────────────────────────────────────────────
static int tcp_connect(int port) {
    struct addrinfo hints = {}, *res = nullptr;
    hints.ai_family   = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[8];
    snprintf(port_str, sizeof(port_str), "%d", port);

    if (getaddrinfo(PC_HOST, port_str, &hints, &res) != 0 || !res) {
        ESP_LOGE(TAG, "DNS: no se resolvió '%s'", PC_HOST);
        return -1;
    }

    int sock = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (sock < 0) { freeaddrinfo(res); return -1; }

    struct timeval tv_s = {.tv_sec = TCP_SEND_TIMEOUT_MS / 1000};
    struct timeval tv_r = {.tv_sec = TCP_RECV_TIMEOUT_MS / 1000};
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv_s, sizeof(tv_s));
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv_r, sizeof(tv_r));

    if (connect(sock, res->ai_addr, res->ai_addrlen) < 0) {
        ESP_LOGE(TAG, "TCP connect falló a %s:%d", PC_HOST, port);
        close(sock);
        freeaddrinfo(res);
        return -1;
    }
    freeaddrinfo(res);
    ESP_LOGI(TAG, "TCP conectado a %s:%d", PC_HOST, port);
    return sock;
}

// ─── TCP: Enviar Bloque PCM Completo ─────────────────────────────────────────
static bool tcp_send_all(int sock, const void* data, size_t len) {
    const uint8_t* p = (const uint8_t*)data;
    while (len > 0) {
        ssize_t sent = send(sock, p, len, 0);
        if (sent <= 0) return false;
        p   += sent;
        len -= sent;
    }
    return true;
}

// ─── TCP: Recibir Respuesta TTS y Enqueue al Speaker ─────────────────────────
static bool tcp_recv_tts_to_speaker(int sock) {
    // Esperar header "KOKO_AUD1" (9 bytes)
    char hdr[9 + 1] = {};
    int  got = recv(sock, hdr, 9, MSG_WAITALL);
    if (got != 9 || strncmp(hdr, TTS_HEADER, 9) != 0) {
        ESP_LOGE(TAG, "TTS header inválido: '%.*s'", got, hdr);
        return false;
    }

    // 4 bytes: longitud del PCM (little-endian)
    uint32_t pcm_len = 0;
    if (recv(sock, &pcm_len, 4, MSG_WAITALL) != 4) return false;
    ESP_LOGI(TAG, "TTS PCM len=%lu bytes (~%.1fs)", (unsigned long)pcm_len,
             (float)pcm_len / (SAMPLE_RATE * 2));

    // Recibir PCM en bloques y encolar al speaker_task
    const size_t CHUNK = I2S_BLOCK_BYTES * 4;
    int16_t* chunk_buf = (int16_t*)heap_caps_malloc(CHUNK, MALLOC_CAP_INTERNAL);
    if (!chunk_buf) return false;

    uint32_t remaining = pcm_len;
    while (remaining > 0) {
        size_t to_read = (remaining < CHUNK) ? remaining : CHUNK;
        int    n       = recv(sock, chunk_buf, to_read, MSG_WAITALL);
        if (n <= 0) break;

        // Enqueue bloques de I2S_BLOCK_SAMPLES al speaker
        int16_t* ptr   = chunk_buf;
        int       left = n / sizeof(int16_t);
        while (left >= I2S_BLOCK_SAMPLES) {
            int16_t* blk = (int16_t*)heap_caps_malloc(I2S_BLOCK_BYTES, MALLOC_CAP_INTERNAL);
            if (blk) {
                memcpy(blk, ptr, I2S_BLOCK_BYTES);
                if (xQueueSend(s_speaker_queue, &blk, pdMS_TO_TICKS(200)) != pdTRUE)
                    heap_caps_free(blk);
            }
            ptr  += I2S_BLOCK_SAMPLES;
            left -= I2S_BLOCK_SAMPLES;
        }
        remaining -= n;
    }
    heap_caps_free(chunk_buf);

    // Señal "fin de TTS"
    int16_t* sentinel = nullptr;
    xQueueSend(s_speaker_queue, &sentinel, portMAX_DELAY);
    return true;
}

// ─── Clasificador Local de Intención (basado en perfil de energía) ────────────
static local_intent_t classify_local_intent(const int16_t* frames,
                                              int n_frames_x_samples) {
    // Dividir en 5 ventanas y calcular RMS de cada una
    float rms[5] = {};
    int   window  = n_frames_x_samples / 5;
    for (int w = 0; w < 5; w++) {
        const int16_t* p = frames + w * window;
        double acc       = 0.0;
        for (int i = 0; i < window; i++) { double s = p[i]; acc += s * s; }
        rms[w] = (float)sqrt(acc / window);
    }
    float avg   = (rms[0] + rms[1] + rms[2] + rms[3] + rms[4]) / 5.0f;
    float peak  = 0.0f;
    int   pi    = 0;
    for (int i = 0; i < 5; i++) { if (rms[i] > peak) { peak = rms[i]; pi = i; } }

    // Reglas heurísticas (se mejoran con entrenamiento real):
    if (peak > 3500.0f && (pi == 0 || pi == 1))  return LOCAL_INTENT_EMERGENCY;
    if (avg  > 2000.0f && pi <= 1)                return LOCAL_INTENT_DOOR_OPEN;
    if (avg  > 2000.0f && pi >= 3)                return LOCAL_INTENT_DOOR_CLOSE;
    if (avg  > 1200.0f && pi == 2)                return LOCAL_INTENT_LIGHT_ON;
    if (avg  >  800.0f && pi >= 3 && peak < 2000) return LOCAL_INTENT_LIGHT_OFF;
    if (n_frames_x_samples > 1500 * I2S_BLOCK_SAMPLES / 160)
                                                   return LOCAL_INTENT_ROBOT;
    return LOCAL_INTENT_UNKNOWN;
}

// ─── Tarea: Turbo Mode Check ──────────────────────────────────────────────────
static void turbo_check_task(void* arg) {
    // Esperar WiFi antes de empezar
    xEventGroupWaitBits(s_evt, EVT_WIFI_CONNECTED, pdFALSE, pdTRUE, portMAX_DELAY);
    ESP_LOGI(TAG, "Turbo check iniciado");

    while (true) {
        esp_http_client_config_t cfg = {};
        cfg.url                      = "http://" PC_HOST ":" "8080" "/ping";
        cfg.timeout_ms               = 900;
        cfg.disable_auto_redirect    = true;

        esp_http_client_handle_t cli = esp_http_client_init(&cfg);
        esp_err_t err = esp_http_client_perform(cli);
        int code      = esp_http_client_get_status_code(cli);
        esp_http_client_cleanup(cli);

        bool prev      = s_turbo_mode;
        s_turbo_mode   = (err == ESP_OK && code == 200);
        if (s_turbo_mode != prev) {
            ESP_LOGI(TAG, "Turbo mode: %s", s_turbo_mode ? "ON ✓" : "OFF (fallback local)");
            led_set(s_turbo_mode ? 0 : 80, s_turbo_mode ? 60 : 0, 5);
        }
        vTaskDelay(pdMS_TO_TICKS(PING_INTERVAL_MS));
    }
}

// ─── Tarea: Speaker (I2S TX, Core 0) ─────────────────────────────────────────
static void speaker_task(void* arg) {
    ESP_LOGI(TAG, "Speaker task listo");
    while (true) {
        int16_t* blk = nullptr;
        if (xQueueReceive(s_speaker_queue, &blk, portMAX_DELAY) != pdTRUE) continue;

        if (blk == nullptr) {
            // Sentinel: playback completo
            xEventGroupSetBits(s_evt, EVT_SPK_DONE);
            led_set(0, 0, 5);
            continue;
        }
        size_t written = 0;
        i2s_channel_write(s_tx_chan, blk, I2S_BLOCK_BYTES, &written, pdMS_TO_TICKS(100));
        heap_caps_free(blk);
    }
}

// ─── Tarea: UART Monitor (respuestas de ESP2) ─────────────────────────────────
static void uart_monitor_task(void* arg) {
    uint8_t rx_buf[256];
    ESP_LOGI(TAG, "UART monitor listo");
    while (true) {
        int len = uart_read_bytes(UART_PORT, rx_buf, sizeof(rx_buf) - 1,
                                  pdMS_TO_TICKS(50));
        if (len <= 0) continue;
        rx_buf[len] = '\0';

        // Parsear "RESP:<texto>\n"
        char* p = (char*)rx_buf;
        if (strncmp(p, UART_RESP_PREFIX, strlen(UART_RESP_PREFIX)) == 0) {
            char* text = p + strlen(UART_RESP_PREFIX);
            char* nl   = strchr(text, '\n');
            if (nl) *nl = '\0';

            xSemaphoreTake(s_uart_mux, portMAX_DELAY);
            strncpy(s_uart_resp_buf, text, sizeof(s_uart_resp_buf) - 1);
            xSemaphoreGive(s_uart_mux);
            xEventGroupSetBits(s_evt, EVT_UART_RESP);
            ESP_LOGI(TAG, "UART←ESP2: '%s'", text);
        }
    }
}

// ─── Tarea: I2S Reader (Core 0, prio alta) ───────────────────────────────────
static void i2s_reader_task(void* arg) {
    int32_t* raw32 = (int32_t*)heap_caps_malloc(
        I2S_BLOCK_SAMPLES * sizeof(int32_t), MALLOC_CAP_INTERNAL);
    int16_t* pcm16 = (int16_t*)heap_caps_malloc(I2S_BLOCK_BYTES, MALLOC_CAP_INTERNAL);
    if (!raw32 || !pcm16) { vTaskDelete(nullptr); return; }

    for (int i = 0; i < WARMUP_READS; i++) {
        size_t br;
        i2s_channel_read(s_rx_chan, raw32, I2S_BLOCK_SAMPLES * 4, &br, portMAX_DELAY);
    }
    ESP_LOGI(TAG, "Mic listo");
    led_set(0, 0, 5);

    while (true) {
        size_t br;
        if (i2s_channel_read(s_rx_chan, raw32, I2S_BLOCK_SAMPLES * 4,
                              &br, portMAX_DELAY) != ESP_OK) continue;
        int n = br / 4;
        for (int i = 0; i < n; i++) {
            int32_t s = raw32[i] >> 8;
            pcm16[i]  = (int16_t)((s > 32767) ? 32767 : (s < -32768) ? -32768 : s);
        }
        xQueueSend(s_i2s_queue, pcm16, 0);
    }
}

// ─── Tarea: Wake Word + Pipeline Post-WW (Core 1) ────────────────────────────
static void wakeword_task(void* arg) {
    const tflite::Model* model = tflite::GetModel(hey_ardo_tflite);
    if (model->version() != TFLITE_SCHEMA_VERSION) {
        ESP_LOGE(TAG, "Modelo TFLM incompatible"); vTaskDelete(nullptr); return;
    }

    tflite::MicroMutableOpResolver<16> resolver;
    resolver.AddFullyConnected();    resolver.AddConv2D();
    resolver.AddDepthwiseConv2D();   resolver.AddReshape();
    resolver.AddSoftmax();           resolver.AddRelu();
    resolver.AddCallOnce();          resolver.AddReadVariable();
    resolver.AddAssignVariable();    resolver.AddVarHandle();
    resolver.AddConcatenation();     resolver.AddStridedSlice();
    resolver.AddSplitV();            resolver.AddLogistic();
    resolver.AddQuantize();          resolver.AddDequantize();

    tflite::MicroAllocator* alloc = tflite::MicroAllocator::Create(
        s_tensor_arena, TENSOR_ARENA_SIZE);
    tflite::MicroResourceVariables* rv = tflite::MicroResourceVariables::Create(alloc, 10);
    tflite::MicroInterpreter interp(model, resolver, alloc, rv);
    if (interp.AllocateTensors() != kTfLiteOk) {
        ESP_LOGE(TAG, "AllocateTensors falló"); vTaskDelete(nullptr); return;
    }

    TfLiteTensor* inp       = interp.input(0);
    TfLiteTensor* out       = interp.output(0);
    int   n_classes          = (int)(out->bytes);
    int   wake_class         = (n_classes > 1) ? 1 : 0;
    float out_scale          = out->params.scale;
    int   out_zp             = out->params.zero_point;
    float in_scale           = (inp->params.scale == 0.0f) ? 1.0f : inp->params.scale;

    int8_t* spec_history = (int8_t*)heap_caps_calloc(inp->bytes, 1, MALLOC_CAP_SPIRAM);
    if (!spec_history) { ESP_LOGE(TAG, "Sin PSRAM"); vTaskDelete(nullptr); return; }

    int     feed_size  = s_afe_handle->get_feed_chunksize(s_afe_data);
    int     fetch_size = s_afe_handle->get_fetch_chunksize(s_afe_data);
    int16_t* afe_buf   = (int16_t*)heap_caps_malloc(feed_size * 2, MALLOC_CAP_INTERNAL);
    int16_t* pcm_block = (int16_t*)heap_caps_malloc(I2S_BLOCK_BYTES, MALLOC_CAP_INTERNAL);
    if (!afe_buf || !pcm_block) { vTaskDelete(nullptr); return; }

    // Buffer de captura post-WW (PSRAM — ~2.5s de audio AFE procesado)
    const int  MAX_CAPTURE_SAMPLES = fetch_size * CMD_CAPTURE_FRAMES;
    int16_t*   capture_buf = (int16_t*)heap_caps_malloc(
        MAX_CAPTURE_SAMPLES * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    int        capture_idx = 0;
    if (!capture_buf) ESP_LOGW(TAG, "Sin PSRAM para captura — fallback limitado");

    SlidingWindow sw;
    sw_reset(&sw);
    int afe_written = 0;

    bool    cooldown     = false;
    int64_t cooldown_end = 0;

    ESP_LOGI(TAG, "WW task lista. Escuchando...");

    while (true) {
        // ── Cooldown ─────────────────────────────────────────────────────────
        if (cooldown) {
            int64_t now = (int64_t)xTaskGetTickCount() * portTICK_PERIOD_MS;
            if (now >= cooldown_end) {
                cooldown = false;
                sw_reset(&sw);
                xSemaphoreTake(s_state_mux, portMAX_DELAY);
                s_state = STATE_IDLE;
                xSemaphoreGive(s_state_mux);
                led_set(0, 0, 5);
                ESP_LOGI(TAG, "Escuchando...");
            }
            // Drenar queue durante cooldown para no atrasarse
            xQueueReceive(s_i2s_queue, pcm_block, pdMS_TO_TICKS(10));
            continue;
        }

        if (xQueueReceive(s_i2s_queue, pcm_block, portMAX_DELAY) != pdTRUE) continue;

        // ── Rellenar buffer AFE ───────────────────────────────────────────────
        int rem = I2S_BLOCK_SAMPLES, src = 0;
        while (rem > 0) {
            int space   = feed_size - afe_written;
            int to_copy = (rem < space) ? rem : space;
            memcpy(afe_buf + afe_written, pcm_block + src, to_copy * 2);
            afe_written += to_copy;
            src         += to_copy;
            rem         -= to_copy;
            if (afe_written < feed_size) break;

            s_afe_handle->feed(s_afe_data, afe_buf);
            afe_written = 0;

            afe_fetch_result_t* r = s_afe_handle->fetch(s_afe_data);
            if (!r || !r->data) continue;

            // ── Detección de Wake Word ────────────────────────────────────────
            if (s_state == STATE_IDLE) {
                if (r->vad_state == VAD_SILENCE) { sw_reset(&sw); continue; }

                size_t used;
                struct FrontendOutput fe = FrontendProcessSamples(
                    &s_fe_state, r->data, fetch_size, &used);
                if (!fe.values || fe.size != NUM_MEL_CHANNELS) continue;

                int8_t frame[NUM_MEL_CHANNELS];
                for (int i = 0; i < NUM_MEL_CHANNELS; i++) {
                    float   fv = (float)fe.values[i] / 26.0f;
                    int32_t qv = (int32_t)roundf(fv / in_scale) + inp->params.zero_point;
                    frame[i]   = (int8_t)((qv > 127) ? 127 : (qv < -128) ? -128 : qv);
                }
                memmove(spec_history, spec_history + NUM_MEL_CHANNELS,
                        inp->bytes - NUM_MEL_CHANNELS);
                memcpy(spec_history + inp->bytes - NUM_MEL_CHANNELS, frame, NUM_MEL_CHANNELS);
                memcpy(inp->data.int8, spec_history, inp->bytes);

                if (interp.Invoke() != kTfLiteOk) continue;
                int8_t raw = out->data.int8[wake_class];
                float  prob = (float)(raw - out_zp) * out_scale;
                if (prob < 0.0f) prob = 0.0f;
                float  avg  = sw_push(&sw, prob);

                if (avg >= PROBABILITY_CUTOFF) {
                    ESP_LOGW(TAG, "*** HEY ARDO! avg=%.3f ***", avg);
                    led_set(0, 200, 0);
                    xSemaphoreTake(s_state_mux, portMAX_DELAY);
                    s_state = STATE_WW_DETECTED;
                    xSemaphoreGive(s_state_mux);
                    sw_reset(&sw);
                    capture_idx = 0;

                    // ── TURBO MODE: Abrir TCP y hacer streaming ───────────────
                    if (s_turbo_mode) {
                        led_set(0, 0, 200);  // azul = streaming
                        xSemaphoreTake(s_state_mux, portMAX_DELAY);
                        s_state = STATE_TURBO_STREAM;
                        xSemaphoreGive(s_state_mux);

                        int sock = tcp_connect(PC_AUDIO_PORT);
                        if (sock < 0) {
                            ESP_LOGE(TAG, "TCP falló — cayendo a fallback");
                            s_turbo_mode = false;
                            goto do_local_capture;
                        }

                        // Enviar header
                        send(sock, TCP_HEADER, 9, 0);

                        // Streaming en tiempo real: continuar leyendo AFE y enviando
                        int   silence_cnt    = 0;
                        int   total_frames   = 0;
                        bool  stream_done    = false;

                        while (!stream_done && total_frames < CMD_CAPTURE_FRAMES * 2) {
                            int16_t* blk2 = nullptr;
                            if (xQueueReceive(s_i2s_queue, pcm_block, pdMS_TO_TICKS(100)) != pdTRUE)
                                break;

                            int rem2 = I2S_BLOCK_SAMPLES, src2 = 0;
                            while (rem2 > 0) {
                                int sp = feed_size - afe_written;
                                int tc = (rem2 < sp) ? rem2 : sp;
                                memcpy(afe_buf + afe_written, pcm_block + src2, tc * 2);
                                afe_written += tc;
                                src2 += tc;
                                rem2 -= tc;
                                if (afe_written < feed_size) break;

                                s_afe_handle->feed(s_afe_data, afe_buf);
                                afe_written = 0;
                                afe_fetch_result_t* r2 = s_afe_handle->fetch(s_afe_data);
                                if (!r2 || !r2->data) continue;

                                // Enviar frame AFE procesado (mejorado, sin ruido)
                                if (!tcp_send_all(sock, r2->data, fetch_size * 2)) {
                                    stream_done = true; break;
                                }
                                total_frames++;

                                // Detectar fin de utterance por silencio
                                if (r2->vad_state == VAD_SILENCE) {
                                    silence_cnt++;
                                    if (silence_cnt >= CMD_SILENCE_FRAMES) stream_done = true;
                                } else {
                                    silence_cnt = 0;
                                }
                            }
                        }
                        afe_written = 0;  // reset AFE buffer

                        // Enviar footer
                        send(sock, TCP_FOOTER, 9, 0);
                        ESP_LOGI(TAG, "Stream enviado (%d frames) — esperando TTS...", total_frames);
                        led_set(200, 150, 0);  // amarillo = esperando respuesta

                        // Recibir TTS del servidor y encolar al speaker
                        bool tts_ok = tcp_recv_tts_to_speaker(sock);
                        close(sock);

                        if (tts_ok) {
                            xSemaphoreTake(s_state_mux, portMAX_DELAY);
                            s_state = STATE_PLAYBACK;
                            xSemaphoreGive(s_state_mux);
                            led_set(0, 200, 200);  // cyan = reproduciendo
                            xEventGroupWaitBits(s_evt, EVT_SPK_DONE,
                                                pdTRUE, pdTRUE, pdMS_TO_TICKS(30000));
                        }
                        goto enter_cooldown;
                    }

do_local_capture:
                    // ── FALLBACK: Capturar audio y clasificar localmente ──────
                    {
                        led_set(200, 80, 0);  // naranja = modo local
                        xSemaphoreTake(s_state_mux, portMAX_DELAY);
                        s_state = STATE_LOCAL_CAPTURE;
                        xSemaphoreGive(s_state_mux);

                        capture_idx     = 0;
                        int silence_cnt = 0;

                        for (int frame_n = 0; frame_n < CMD_CAPTURE_FRAMES; frame_n++) {
                            if (xQueueReceive(s_i2s_queue, pcm_block, pdMS_TO_TICKS(50)) != pdTRUE)
                                break;

                            int rem2 = I2S_BLOCK_SAMPLES, src2 = 0;
                            while (rem2 > 0) {
                                int sp = feed_size - afe_written;
                                int tc = (rem2 < sp) ? rem2 : sp;
                                memcpy(afe_buf + afe_written, pcm_block + src2, tc * 2);
                                afe_written += tc; src2 += tc; rem2 -= tc;
                                if (afe_written < feed_size) break;

                                s_afe_handle->feed(s_afe_data, afe_buf);
                                afe_written = 0;
                                afe_fetch_result_t* r2 = s_afe_handle->fetch(s_afe_data);
                                if (!r2 || !r2->data) continue;

                                if (capture_buf && capture_idx + fetch_size <= MAX_CAPTURE_SAMPLES) {
                                    memcpy(capture_buf + capture_idx, r2->data, fetch_size * 2);
                                    capture_idx += fetch_size;
                                }

                                if (r2->vad_state == VAD_SILENCE) {
                                    if (++silence_cnt >= CMD_SILENCE_FRAMES && capture_idx > fetch_size * 5)
                                        goto capture_done;
                                } else {
                                    silence_cnt = 0;
                                }
                            }
                        }
capture_done:
                        afe_written = 0;

                        // Clasificar intent localmente
                        local_intent_t intent = LOCAL_INTENT_UNKNOWN;
                        if (capture_buf && capture_idx > 0)
                            intent = classify_local_intent(capture_buf, capture_idx);

                        const char* cmd_text = (intent < LOCAL_INTENT_UNKNOWN)
                            ? LOCAL_INTENT_CMDS[intent] : "comando desconocido";
                        ESP_LOGI(TAG, "Intent local: [%d] '%s'", (int)intent, cmd_text);

                        // Enviar a ESP2
                        char uart_msg[160];
                        snprintf(uart_msg, sizeof(uart_msg), "%s%s\n", UART_CMD_PREFIX, cmd_text);
                        uart_write_bytes(UART_PORT, uart_msg, strlen(uart_msg));

                        xSemaphoreTake(s_state_mux, portMAX_DELAY);
                        s_state = STATE_WAITING_ESP2;
                        xSemaphoreGive(s_state_mux);
                        xEventGroupClearBits(s_evt, EVT_UART_RESP);

                        // Esperar respuesta de ESP2
                        EventBits_t bits = xEventGroupWaitBits(
                            s_evt, EVT_UART_RESP, pdTRUE, pdTRUE,
                            pdMS_TO_TICKS(UART_RESP_TIMEOUT_MS));

                        if (bits & EVT_UART_RESP) {
                            // ESP2 respondió — por ahora solo loguear
                            // (Para TTS con clips de SPIFFS, aquí se cargaría el clip)
                            xSemaphoreTake(s_uart_mux, portMAX_DELAY);
                            ESP_LOGI(TAG, "Respuesta ESP2: '%s'", s_uart_resp_buf);
                            xSemaphoreGive(s_uart_mux);
                            led_set(0, 200, 0);
                        } else {
                            ESP_LOGW(TAG, "ESP2 no respondió (timeout)");
                            led_set(200, 0, 0);
                        }
                        goto enter_cooldown;
                    }

enter_cooldown:
                    cooldown     = true;
                    cooldown_end = (int64_t)xTaskGetTickCount() * portTICK_PERIOD_MS
                                   + COOLDOWN_MS;
                    xSemaphoreTake(s_state_mux, portMAX_DELAY);
                    s_state = STATE_COOLDOWN;
                    xSemaphoreGive(s_state_mux);
                    sw_reset(&sw);
                }
            }
        }
    }
}

// ═════════════════════════════════════════════════════════════════════════════
extern "C" void app_main() {
    ESP_LOGI(TAG, "=== Ardo v2 — ESP1 Oído+Boca | ESP32-S3 ===");

    // Primitivos de sincronización
    s_evt       = xEventGroupCreate();
    s_state_mux = xSemaphoreCreateMutex();
    s_uart_mux  = xSemaphoreCreateMutex();

    init_led();

    // Tensor arena en PSRAM o internal RAM
    s_tensor_arena = (uint8_t*)heap_caps_malloc(TENSOR_ARENA_SIZE,
                                                  MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_tensor_arena)
        s_tensor_arena = (uint8_t*)heap_caps_malloc(TENSOR_ARENA_SIZE,
                                                     MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!s_tensor_arena) { ESP_LOGE(TAG, "SIN MEMORIA para tensor arena"); return; }

    ESP_LOGI(TAG, "RAM interna: %d | PSRAM: %d",
             heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             heap_caps_get_free_size(MALLOC_CAP_SPIRAM));

    init_microphone();
    init_speaker();
    init_afe();
    if (!init_frontend()) return;
    init_wifi();
    init_uart();

    s_i2s_queue     = xQueueCreate(I2S_QUEUE_DEPTH, I2S_BLOCK_BYTES);
    s_speaker_queue = xQueueCreate(32, sizeof(int16_t*));
    if (!s_i2s_queue || !s_speaker_queue) { ESP_LOGE(TAG, "Queues fallaron"); return; }

    led_set(0, 0, 15);

    // Tasks
    xTaskCreatePinnedToCore(i2s_reader_task,   "i2s",      4096,  nullptr, 8, nullptr, 0);
    xTaskCreatePinnedToCore(speaker_task,      "speaker",  4096,  nullptr, 5, nullptr, 0);
    xTaskCreatePinnedToCore(turbo_check_task,  "turbo",    4096,  nullptr, 2, nullptr, 0);
    xTaskCreatePinnedToCore(uart_monitor_task, "uart_mon", 2048,  nullptr, 4, nullptr, 0);
    xTaskCreatePinnedToCore(wakeword_task,     "wakeword", 32768, nullptr, 6, nullptr, 1);

    ESP_LOGI(TAG, "Sistema listo.");
}
