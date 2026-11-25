// SPDX-License-Identifier: Apache-2.0
/*
 * firmware-ci's baseline app: start the BLE stack, arm a button wake, sleep.
 *
 * The BLE stack is started so that the image carries a radio stack, as a handheld
 * that talks to a phone does: the image size against its app slot is then a real
 * figure, and a transmit-power reading has a built setting to read. The button is
 * on GPIO0, an RTC GPIO on the ESP32-S3, so ext0 can wake the chip from deep sleep.
 */
#include "driver/rtc_io.h"
#include "esp_sleep.h"
#include "nimble/nimble_port.h"

void app_main(void)
{
    if (nimble_port_init() == ESP_OK) {
        nimble_port_deinit();
    }
    rtc_gpio_pullup_en(GPIO_NUM_0);
    esp_sleep_enable_ext0_wakeup(GPIO_NUM_0, 0);
    esp_deep_sleep_start();
}
