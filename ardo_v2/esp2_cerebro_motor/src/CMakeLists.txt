# Solo compilar fuentes del firmware — excluir archivos de test en host
idf_component_register(
    SRCS         "main.cpp" "tiny_nlu.cpp"
    INCLUDE_DIRS "."
    REQUIRES     led_strip driver
)
