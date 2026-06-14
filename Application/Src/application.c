#include <application.h>

key_bullet_t Keys[KEY_NUMBER];
bool Keys_pressed[KEY_NUMBER];
VOFA_REPORT vofa;
key_packet_t key_packet;
frame_header_t frame_header;
frame_body_tail_t frame_body_tail;
referee_packet_t referee_packet;
volatile uint32_t ms_counter = 0;

void init_keys(){
    memset(Keys, 0, sizeof(Keys));
    memset(Keys_pressed, 0, sizeof(Keys_pressed));
    Keys[0].key = 3; // PA3
    Keys[1].key = 4; // PA4
    Keys[2].key = 5; // PA5
    Keys[0].small_bullet_x_pos = 1160;
    Keys[0].small_bullet_y_pos = 560;
}

void init_vofa(){
    vofa.tail[0] = 0x00;
    vofa.tail[1] = 0x00;
    vofa.tail[2] = 0x80;
    vofa.tail[3] = 0x7F;
}

void init_packet(){
    // for custom keyboard the command id will be 0x0306 with fixed 8 byte data, the size of packet is known
    // we can fill the packet header in starting stage:
    memset(&key_packet, 0, sizeof(key_packet));
    memset(&frame_header, 0, sizeof(frame_header));
    memset(&frame_body_tail, 0, sizeof(frame_body_tail));
    memset(&referee_packet, 0, sizeof(referee_packet));

    frame_header.SOF = 0xA5; // fixed
    frame_header.data_length = sizeof(key_packet_t); // 8 bytes
    frame_header.seq = 0; // packet sequence starts from any value, then increments per packet
    frame_header.crc8 = Get_CRC8_Check_Sum((uint8_t *)&frame_header,
                                           sizeof(frame_header) - sizeof(frame_header.crc8),
                                           CRC8_INIT);

    frame_body_tail.cmd_id = 0x0306;
    memcpy(frame_body_tail.data, &key_packet, sizeof(key_packet));

    referee_packet.fht = frame_header;
    referee_packet.fbt = frame_body_tail;
    referee_packet.fbt.frame_tail = Get_CRC16_Check_Sum((uint8_t *)&referee_packet,
                                                        sizeof(referee_packet) - sizeof(referee_packet.fbt.frame_tail),
                                                        CRC16_INIT);
}

// this function used for debug!
void loop_report(){
    bool is_small_bullet = (bool)HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_6);

    vofa.val[0] = (float)Keys[0].key;
    vofa.val[1] = (float)Keys[0].big_bullet_x_pos;
    vofa.val[2] = (float)is_small_bullet;
    vofa.val[3] = (float)Keys_pressed[0];
    HAL_UART_Transmit_DMA(&huart1, (void*)&vofa, sizeof(vofa));
    ms_counter++;
}

void wait_ms(int time_ms){
    ms_counter = 0;
    while (ms_counter < time_ms);
}

// scan for keys that is triggered:
void loop_scan_keys(){
    // read for pin PA3 PA4 PA5:
    for (int index = 0; index < KEY_NUMBER; index++){
        if (!HAL_GPIO_ReadPin(GPIOA, (uint16_t)(1U << (index + 3)))){
            wait_ms(DEBOUNCE_DELAY);
            Keys_pressed[index] = !HAL_GPIO_ReadPin(GPIOA, (uint16_t)(1U << (index + 3)));
        } else {
            Keys_pressed[index] = 0;
        }
    }
}
