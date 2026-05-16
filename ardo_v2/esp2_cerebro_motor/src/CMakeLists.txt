FILE(GLOB_RECURSE app_sources "*.c" "*.cpp" "*.cc")

idf_component_register(
    SRCS         ${app_sources}
    INCLUDE_DIRS "."
    REQUIRES     led_strip driver
)
