#ifndef __APPLICATION_H
#define __APPLICATION_H

#include <ws2812.h>
#include <utils.h>
#include <usart.h>
#include <gpio.h>
#include <stm32f103xb.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>

#define DEBOUNCE_DELAY 25 // in terms of ms
#define KEY_NUMBER 3

typedef struct key_bullet_mapping{
    int key; // which key
    int small_bullet_x_pos; // x pos on screen for small bullet
    int small_bullet_y_pos; // y pos on screen for big bullet
    int big_bullet_x_pos; // x pos on screen for big bullet 
    int big_bullet_y_pos; // y pos on screen for big bullet 
} key_bullet_t;

extern key_bullet_t Keys[KEY_NUMBER];
extern bool Keys_pressed[KEY_NUMBER];
extern volatile uint32_t ms_counter;
extern volatile bool is_small_bullet;
extern volatile int key_need_handle;

typedef struct __attribute__((packed)) {
    float val[5];
    uint8_t tail[4];
} VOFA_REPORT; 

extern VOFA_REPORT vofa;

typedef struct __attribute__((packed)) {
    uint16_t key_value;
    uint16_t x_position:12;
    uint16_t mouse_left:4;
    uint16_t y_position:12; 
    uint16_t mouse_right:4;
    uint16_t reserved;
} key_packet_t;

extern key_packet_t key_packet;

typedef struct __attribute__((packed)) {
    uint8_t SOF; // fixed 0xA5
    uint16_t data_length;
    uint8_t seq;
    uint8_t crc8;
} frame_header_t;

extern frame_header_t frame_header;

typedef struct __attribute__((packed)) {
    uint16_t cmd_id;
    uint8_t data[8]; // 8 byte for this case, use memmove to move from packet to data
    uint16_t frame_tail;
} frame_body_tail_t;

extern frame_body_tail_t frame_body_tail;

typedef struct __attribute__((packed)) {
    frame_header_t fht;
    frame_body_tail_t fbt;
} referee_packet_t;

extern referee_packet_t referee_packet;

void init_keys();
void init_vofa();
void init_packet();
void loop_report();
void loop_scan_keys();
void wait_ms(int time_ms);
void mouse_control(uint16_t x, uint16_t y, bool click);

// following function each they will send packets to referee system
void call_buy_UI();
void move_click_mouse(int key);
void click_buy_bullets();


#endif
