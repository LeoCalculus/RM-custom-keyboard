#include <ws2812.h>

#define WS2812_SPI &hspi2

WS_data ws2812_spibuf[WS_RESET_PERIODS + LED_NUMS + WS_RESET_PERIODS];
uint8_t RGB_waveform[RGB_WAVELENGTH];

/**
 * @brief Initialize ws2812 data, calculate RGB waveform, etc.
 * 
 * @param wave_brightness Brightness of RGB wave, 0~1
 */
void ws2812_init(float wave_brightness){
    for(int i=0;i<RGB_WAVELENGTH;i++){
        float omega=((float)i)/RGB_WAVELENGTH*2*3.1415926;
        RGB_waveform[i]=(uint8_t)(255.0f*wave_brightness*(0.5+cosf(omega)/2));
    }
}

/**
 * @brief Update colour to LED strip
 * 
 */
void ws2812_refresh(void){
    //ws2812_pure(5,20,10);
    if (HAL_SPI_GetState(WS2812_SPI) == HAL_SPI_STATE_READY) {
        HAL_SPI_Transmit_DMA(WS2812_SPI,(uint8_t *)ws2812_spibuf, sizeof(ws2812_spibuf));
    }
    
    return;
}

/**
 * @brief Reset LEDs to all black
 * 
 */
void ws2812_resetbuf(void){
    memset(ws2812_spibuf,0,sizeof(ws2812_spibuf));
}

/**
 * @brief Generate SPI data for a given RGB value.
 * 
 * @param setr Red
 * @param setg Green
 * @param setb Blue
 * @return WS_data 
 */
WS_data ws2812_getData(uint8_t setr, uint8_t setg, uint8_t setb){
    WS_data data;
    for(int j=0;j<4;j++){
        data.r[j] = (setr & 0x80 ? WS_1 : WS_0) << 4;
        data.r[j] |= (setr & 0x40 ? WS_1 : WS_0) ;
        setr <<= 2;

        data.g[j] = (setg & 0x80 ? WS_1 : WS_0) << 4;
        data.g[j] |= (setg & 0x40 ? WS_1 : WS_0) ;
        setg <<= 2;

        data.b[j] = (setb & 0x80 ? WS_1 : WS_0) << 4;
        data.b[j] |= (setb & 0x40 ? WS_1 : WS_0) ;
        setb <<= 2;
    }
    return data;
}

/**
 * @brief Display pure colour on LEDs
 * 
 * @param r red
 * @param g green
 * @param b blue
 */
void ws2812_pure(uint8_t r, uint8_t g, uint8_t b){
    ws2812_resetbuf();
    for(int i=0;i<LED_NUMS;i++){
        ws2812_spibuf[WS_RESET_PERIODS + i]=ws2812_getData(r,g,b);
    }
}

/**
 * @brief Generate a RGB "wave" to ws2812 buffer
 * 
 * @param phase phase of "wave". Unit: nums of LED
 */
void ws2812_rgbwave(int phase){
    ws2812_resetbuf();
    for(int i=0;i<LED_NUMS;i++){
        uint8_t r = RGB_waveform[(i+phase)%RGB_WAVELENGTH];
        uint8_t g = RGB_waveform[(i+phase+RGB_WAVELENGTH/3)%RGB_WAVELENGTH];
        uint8_t b = RGB_waveform[(i+phase+RGB_WAVELENGTH*2/3)%RGB_WAVELENGTH];
        ws2812_spibuf[WS_RESET_PERIODS + i] = ws2812_getData(r,g,b);
    }
}

/**
 * @brief Generate a RGB "PWM" wave.
 * 
 * @param phase phase of "wave". Unit: nums of LED
 * @param pulselength pulse length Unit: nums of LED
 * @param r red
 * @param g green
 * @param b blue
 */
void ws2812_rgbpwmwave(int phase, int pulselength, uint8_t r, uint8_t g, uint8_t b){
    ws2812_resetbuf();
    WS_data pulsecolour=ws2812_getData(r,g,b);
    WS_data dark=ws2812_getData(0,0,0);
    for(int i=0;i<LED_NUMS;i++){
        if((i+phase)%RGBPWM_WAVELENGTH<pulselength){
            ws2812_spibuf[WS_RESET_PERIODS + i] = pulsecolour;
        }else{
            ws2812_spibuf[WS_RESET_PERIODS + i] = dark;
        }
    }
}
