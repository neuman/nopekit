# SPDX-License-Identifier: Apache-2.0
idf_component_register(SRCS "main.c"
                       PRIV_REQUIRES bt esp_driver_gpio esp_hw_support)
