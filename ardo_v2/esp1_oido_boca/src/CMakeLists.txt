FILE(GLOB_RECURSE app_sources "*.c" "*.cpp" "*.cc")

idf_component_register(
    SRCS            ${app_sources}
    INCLUDE_DIRS    "."
    REQUIRES        esp-sr esp-tflite-micro led_strip esp_wifi esp_http_client
                    nvs_flash esp_netif driver
)
