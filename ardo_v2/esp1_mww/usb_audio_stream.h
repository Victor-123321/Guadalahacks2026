#pragma once

#include <cstdint>
#include <vector>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include <cstdio>

static SemaphoreHandle_t s_stdout_mutex = nullptr;

void init_usb_stream() {
    if (s_stdout_mutex == nullptr) {
        s_stdout_mutex = xSemaphoreCreateMutex();
    }
}

void send_usb_frame(uint8_t cmd, const uint8_t* data, size_t len) {
    if (s_stdout_mutex == nullptr) return;
    
    // Usamos un mutex para que los logs de texto de ESPHome no se mezclen 
    // a la mitad de nuestros paquetes binarios de audio.
    if (xSemaphoreTake(s_stdout_mutex, portMAX_DELAY) == pdTRUE) {
        uint8_t header[5] = {0xAA, 0xBB, cmd, (uint8_t)(len >> 8), (uint8_t)(len & 0xFF)};
        fwrite(header, 1, 5, stdout);
        
        if (len > 0 && data != nullptr) {
            fwrite(data, 1, len, stdout);
        }
        fflush(stdout);
        xSemaphoreGive(s_stdout_mutex);
    }
}

void send_usb_audio(const uint8_t* data, size_t len) {
    send_usb_frame(0x01, data, len); // 0x01 es el comando de "Audio"
}

void stop_usb_stream() {
    send_usb_frame(0x02, nullptr, 0); // 0x02 es el comando de "Fin de Frase"
}
