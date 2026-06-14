#include <application.h>

key_bullet_t Keys[KEY_NUMBER];
bool Keys_pressed[KEY_NUMBER];
VOFA_REPORT vofa;
key_packet_t key_packet;
frame_header_t frame_header;
frame_body_tail_t frame_body_tail;
referee_packet_t referee_packet;
volatile uint32_t ms_counter = 0;
volatile bool is_small_bullet = 0;
volatile int key_need_handle = -1; // no key need to ne handled when it is -1

#define PACKET_SEND_DELAY 34
#define KEY_FALL_BLOCK_MS 200U
#define SMALL_BULLET_KEY 'P'
#define BIG_BULLET_KEY 'P'

#define BUY_BUTTON_X 0
#define BUY_BUTTON_Y 0
#define CONFIRM_BUTTON_X 0
#define CONFIRM_BUTTON_Y 0

static void update_packet_and_send(void);
static volatile bool long_pressed = 0;

void init_keys(){
    memset(Keys, 0, sizeof(Keys));
    memset(Keys_pressed, 0, sizeof(Keys_pressed));
    Keys[0].key = 3; // PA3
    Keys[1].key = 4; // PA4
    Keys[2].key = 5; // PA5
    Keys[0].small_bullet_x_pos = 491;
    Keys[0].small_bullet_y_pos = 675;
    Keys[0].big_bullet_x_pos = 2036;
    Keys[0].big_bullet_y_pos = 671;
    Keys[1].small_bullet_x_pos = 0;
    Keys[1].small_bullet_y_pos = 0;
    Keys[1].big_bullet_x_pos = 0;
    Keys[1].big_bullet_y_pos = 0;
    Keys[2].small_bullet_x_pos = 0;
    Keys[2].small_bullet_y_pos = 0;
    Keys[2].big_bullet_x_pos = 0;
    Keys[2].big_bullet_y_pos = 0;
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
    vofa.val[0] = (float)Keys[0].key;
    vofa.val[1] = (float)key_need_handle;
    vofa.val[2] = (float)is_small_bullet;
    vofa.val[3] = (float)Keys_pressed[0];
    HAL_UART_Transmit_DMA(&huart1, (void*)&vofa, sizeof(vofa));
    ms_counter++;
}

void wait_ms(int time_ms){
    uint32_t start_tick = HAL_GetTick();

    while ((uint32_t)(HAL_GetTick() - start_tick) < (uint32_t)time_ms);
}

// scan for keys that is triggered:
void loop_scan_keys(){
    static uint32_t key_fall_block_until = 0;
    uint32_t now = HAL_GetTick();

    // also check for small bullet
    is_small_bullet = (bool)HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_6);

    if ((int32_t)(now - key_fall_block_until) < 0){
        memset(Keys_pressed, 0, sizeof(Keys_pressed)); // block all negedge if last negedge was detected
        return;
    }

    // read for pin PA3(0) PA4(1) PA5(2):
    for (int index = 0; index < KEY_NUMBER; index++){
        bool is_pressed = !HAL_GPIO_ReadPin(GPIOA, (uint16_t)(1U << (index + 3)));

        if (is_pressed && !Keys_pressed[index]){
            wait_ms(DEBOUNCE_DELAY);
            // after delay, if still pressed -> true else false
            is_pressed = !HAL_GPIO_ReadPin(GPIOA, (uint16_t)(1U << (index + 3)));
            if (is_pressed && !long_pressed){
                key_fall_block_until = HAL_GetTick() + KEY_FALL_BLOCK_MS;
                key_need_handle = index;
                long_pressed = 1;
                memset(Keys_pressed, 0, sizeof(Keys_pressed));
                return;
            }
        } else {
            long_pressed = 0;
        }

        if (is_pressed){
            Keys_pressed[index] = true;
        } else {
            Keys_pressed[index] = false;
        }
    }
}

void call_buy_UI(){
    if (is_small_bullet){
        key_packet.key_value = SMALL_BULLET_KEY;
    } else { // big bullet 
        key_packet.key_value = BIG_BULLET_KEY;
    }

    key_packet.x_position = 0;
    key_packet.y_position = 0;
    key_packet.mouse_left = 0;
    key_packet.mouse_right = 0;
    key_packet.reserved = 0;
    update_packet_and_send();

    // release O/I after opening the buy UI.
    key_packet.key_value = 0;
    update_packet_and_send();
}

void mouse_control(uint16_t x, uint16_t y, bool click){
    // packet update for mouse
    key_packet.key_value = 0;
    key_packet.x_position = x;
    key_packet.y_position = y;
    key_packet.mouse_left = click ? 1 : 0;
    key_packet.mouse_right = 0;
    key_packet.reserved = 0;
    update_packet_and_send();
    // after send add delay
    wait_ms(PACKET_SEND_DELAY);
}

void move_click_mouse(int key){
    uint16_t x = 0;
    uint16_t y = 0;

    switch (key)
    {
        case 0:
            if (is_small_bullet){
                x = Keys[0].small_bullet_x_pos;
                y = Keys[0].small_bullet_y_pos;
            } else {
                x = Keys[0].big_bullet_x_pos;
                y = Keys[0].big_bullet_y_pos;
            }
            break;
        
        case 1:
            if (is_small_bullet){
                x = Keys[1].small_bullet_x_pos;
                y = Keys[1].small_bullet_y_pos;
            } else {
                x = Keys[1].big_bullet_x_pos;
                y = Keys[1].big_bullet_y_pos;
            }
            break;

        case 2:
            if (is_small_bullet){
                x = Keys[2].small_bullet_x_pos;
                y = Keys[2].small_bullet_y_pos;
            } else {
                x = Keys[2].big_bullet_x_pos;
                y = Keys[2].big_bullet_y_pos;
            }
            break;
        
        default:
            break;
    }

    if (x == 0 && y == 0){
        // Position not filled yet. Do not click the top-left corner by accident.
        return;
    }

    mouse_control(x, y, false);
    mouse_control(x, y, true);
    mouse_control(x, y, false);
}

void click_buy_bullets(){

    mouse_control(BUY_BUTTON_X, BUY_BUTTON_Y, false);
    mouse_control(BUY_BUTTON_X, BUY_BUTTON_Y, true);
    mouse_control(BUY_BUTTON_X, BUY_BUTTON_Y, false);

    mouse_control(CONFIRM_BUTTON_X, CONFIRM_BUTTON_Y, false);
    mouse_control(CONFIRM_BUTTON_X, CONFIRM_BUTTON_Y, true);
    mouse_control(CONFIRM_BUTTON_X, CONFIRM_BUTTON_Y, false);
}

static void update_packet_and_send(void){
    memcpy(frame_body_tail.data, &key_packet, sizeof(key_packet));

    frame_header.crc8 = Get_CRC8_Check_Sum((uint8_t *)&frame_header,
                                           sizeof(frame_header) - sizeof(frame_header.crc8),
                                           CRC8_INIT);

    referee_packet.fht = frame_header;
    referee_packet.fbt = frame_body_tail;
    referee_packet.fbt.frame_tail = Get_CRC16_Check_Sum((uint8_t *)&referee_packet,
                                                        sizeof(referee_packet) - sizeof(referee_packet.fbt.frame_tail),
                                                        CRC16_INIT);

    if (HAL_UART_Transmit_DMA(&huart3, (uint8_t *)&referee_packet, sizeof(referee_packet)) == HAL_OK){
        wait_ms(PACKET_SEND_DELAY);
        frame_header.seq++;
    }
}
