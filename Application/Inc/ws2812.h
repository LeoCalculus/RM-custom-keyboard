#ifndef __WH2812_H
#define __WH2812_H

#include <spi.h>
#include <stdint.h>
#include <math.h>
#include <string.h>

#define LED_NUMS 3
#define WS_RESET_PERIODS 5

#define RGB_WAVELENGTH 160
#define RGBPWM_WAVELENGTH 15

#define WS_1  0x0E
#define WS_0  0x08

#define WS_11 0xEE
#define WS_00 0x88

//Disable struct align
#pragma pack (1)
typedef struct ws_data
{
    uint8_t g[4];
    uint8_t r[4];
    uint8_t b[4];
} WS_data;
#pragma pack ()

void ws2812_init(float wave_brightness);
void ws2812_refresh(void);
void ws2812_pure(uint8_t r, uint8_t g, uint8_t b);
void ws2812_rgbwave(int phase);
void ws2812_rgbpwmwave(int phase, int pulselength, uint8_t r, uint8_t g, uint8_t b);

#endif