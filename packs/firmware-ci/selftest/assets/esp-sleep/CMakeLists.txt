# SPDX-License-Identifier: Apache-2.0
# firmware-ci's baseline: a small ESP-IDF app that starts BLE, arms a wake source and
# enters deep sleep. PROJECT_VER is stated, so the image's app description never
# holds a `git describe` of whatever tree the replay copied.
cmake_minimum_required(VERSION 3.16)
set(PROJECT_VER "1.0.0")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(sleeper)
