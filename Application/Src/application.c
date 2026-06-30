#include <application.h>

key_bullet_t Keys[KEY_NUMBER];
bool Keys_pressed[KEY_NUMBER];
VOFA_REPORT vofa;
key_packet_t key_packet;
frame_header_t frame_header;
frame_body_tail_t frame_body_tail;
referee_packet_t referee_packet;
volatile uint32_t ms_counter = 0;
volatile bool is_small_bullet = 0; // true -> small bullet
volatile int key_need_handle = -1; // no key need to ne handled when it is -1

#define PACKET_SEND_DELAY 28
#define KEY_FALL_BLOCK_MS 200U
#define SMALL_BULLET_KEY 'O'
#define BIG_BULLET_KEY 'I'

#define BUY_BUTTON_X 1275
#define BUY_BUTTON_Y 990
#define CONFIRM_BUTTON_X 1190
#define CONFIRM_BUTTON_Y 865

// #define BUY_BUTTON_X 960
// #define BUY_BUTTON_Y 379

// #define CONFIRM_BUTTON_X 893
// #define CONFIRM_BUTTON_Y 491

#define PA5_BUY_BIG 8
#define PA4_BUY_BIG 10
#define PA3_BUY_BIG 12
#define PA5_BUY_SMALL 80 
#define PA4_BUY_SMALL 100
#define PA3_BUY_SMALL 120

#define BREATH_MIN    10
#define BREATH_MAX    25
#define BREATH_SPEED  40

static void update_packet_and_send(void);
volatile bool long_pressed[3];
static volatile int phase = 10;
static volatile int count_direction = 1; // 1 for up, -1 for decrease
static volatile uint8_t breath_tick = 0;

// big : 8 10 12
// small: 80 100 120
// 1: (1405,860); 2:(1490,860); 5:(1475,860); 10:(1665,860)
// -1: (1150,860); -2: (1070,860); -5: (980,860); -10: (895,860)
// 10: (1405,860); 20:(1490,860); 50:(1475,860); 100:(1665,860)
// -10: (1150,860); -20: (1070,860); -50: (980,860); -100: (895,860)
// #define BUY_ONE_X 1450
// #define BUY_NEG_ONE_X 1150
// #define BUY_Y 860

#define BUY_Y 860

void init_keys(){
    memset(Keys, 0, sizeof(Keys));
    memset(Keys_pressed, 0, sizeof(Keys_pressed));
    Keys[0].key = 3; // PA3
    Keys[1].key = 4; // PA4
    Keys[2].key = 5; // PA5
    Keys[0].small_bullet_x_pos = 491;
    Keys[0].small_bullet_y_pos = 675;
    Keys[0].big_bullet_x_pos = 1580;
    Keys[0].big_bullet_y_pos = 860;
    Keys[1].small_bullet_x_pos = 0;
    Keys[1].small_bullet_y_pos = 0;
    Keys[1].big_bullet_x_pos = 1495;
    Keys[1].big_bullet_y_pos = 860;
    Keys[2].small_bullet_x_pos = 0;
    Keys[2].small_bullet_y_pos = 0;
    Keys[2].big_bullet_x_pos = 1405;
    Keys[2].big_bullet_y_pos = 860;
}

// inot sota
// void get_key_pattern(int num){
//     // -2 for 0
//     // -1 for 1
//     //  5 for 2
//     //  1 for 3
//     //  2 for 4
//     // 10 for 5

//     //if ball type is true, than big ball, else, small ball
//     if(is_small_bullet){
//         num/=10;
//     }
//     int a[6] = {0};
//     int temp;
//     // get how many ten needed
//     a[5] = num / 10;
//     temp = num % 10;
//     //if remaining is between those number, it can be done in two operations
//     if(temp >= 3 && temp <= 7){
//         temp=temp-5;
//         a[2]+=1;
//         //-2 -1 0 1 2
//         temp+=2;
//         //prevent double count
//         a[temp]=(temp!=2)?1:0;
//     } 
//     // if 8 or 9, eg 8-8 give the index 0, which corrispond to -2
//     else if(temp>=5){
//         temp-=8;
//         a[temp]+=1;
//     }
//     // if 1 or 2, eg, 1+3 = index 4, corrispond to + 1
//     else{
//         a[temp+3]=1;
//     }
//     for(int i=0;i<6;i++){
//         for(int j=0;j<a[i];j++){
//             if(i<=1){
//                 mouse_control(BUY_NEG_ONE_X - (1-i)*90, BUY_Y, true);
//                 temp = BUY_NEG_ONE_X - (1-i)*90;
//             }
//             else{
//                 mouse_control(BUY_ONE_X + (i-2)*90, BUY_Y, true);
//                 temp = BUY_NEG_ONE_X + (i-2)*90;
//             }            
//         }
//         mouse_control(temp, BUY_Y, false);
//     }
 
// }

/*
 * a[i] 的含义：
 * 0: -2
 * 1: -1
 * 2: +5
 * 3: +1
 * 4: +2
 * 5: +10
 *
 * amount 是实际购买数量：
 * 大弹：8、12、26 ...
 * 小弹：80、120、260 ...
 */
bool get_key_pattern(uint32_t amount)
{
    // static const uint16_t button_x[6] = {
    //     1070,  // -2
    //     1150,  // -1
    //     1475,  // +5
    //     1405,  // +1
    //     1490,  // +2
    //     1665,  // +10
    // };
    static const uint16_t button_x[6] = {
        1095,   // -2，原 1070
        1170,   // -1，原 1150
        1545,  // +5，原 1475
        1395,  // +1，原 1405
        1470,  // +2，原 1490
        1620,  // +10，原 1665
    };

    /*
     * 正数必须先点，避免从 0 开始时负数按钮被 UI 截断。
     * 8 会先点 +10，最后点 -2。
     */
    static const uint8_t press_order[6] = {
        5,  // +10
        2,  // +5
        4,  // +2
        3,  // +1
        1,  // -1
        0,  // -2
    };

    uint32_t a[6] = {0};
    uint32_t unit_amount;
    uint32_t remainder;

    /*
     * 小弹面板只有 ±10/±20/±50/±100，
     * 所以非 10 倍数无法精确购买。
     */
    if (is_small_bullet) {
        if ((amount % 10U) != 0U) {
            return false;
        }
        unit_amount = amount / 10U;
    } else {
        unit_amount = amount;
    }

    a[5] = unit_amount / 10U;
    remainder = unit_amount % 10U;

    switch (remainder) {
        case 0:
            break;

        case 1:
            a[3]++;              // +1
            break;

        case 2:
            a[4]++;              // +2
            break;

        case 3:
            a[2]++; a[0]++;      // +5 -2
            break;

        case 4:
            a[2]++; a[1]++;      // +5 -1
            break;

        case 5:
            a[2]++;              // +5
            break;

        case 6:
            a[2]++; a[3]++;      // +5 +1
            break;

        case 7:
            a[2]++; a[4]++;      // +5 +2
            break;

        case 8:
            a[5]++; a[0]++;      // +10 -2
            break;

        case 9:
            a[5]++; a[1]++;      // +10 -1
            break;

        default:
            return false;
    }

    for (uint32_t order = 0; order < 6U; order++) {
        uint32_t index = press_order[order];

        for (uint32_t count = 0; count < a[index]; count++) {

            mouse_control(button_x[index], BUY_Y, false);
            mouse_control(button_x[index], BUY_Y, true);
            mouse_control(button_x[index], BUY_Y, false);
        }
    }

    return true;
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
    // phase++;
    // ws2812_rgbwave(phase/10);
    
    if (++breath_tick >= BREATH_SPEED) {
        breath_tick = 0;

        phase += count_direction;

        if (phase >= BREATH_MAX) {
            phase = BREATH_MAX;
            count_direction = -1;
        } else if (phase <= BREATH_MIN) {
            phase = BREATH_MIN;
            count_direction = 1;
        }
    }

    uint8_t brightness = phase;

    if (is_small_bullet) {
        ws2812_pure(0, brightness, 0); // small bullet for green 
    } else { 
        ws2812_pure(brightness, brightness, brightness);
    }

    ws2812_refresh();
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
            if (is_pressed && !long_pressed[index]){
                key_fall_block_until = HAL_GetTick() + KEY_FALL_BLOCK_MS;
                key_need_handle = index;
                long_pressed[index] = 1;
                memset(Keys_pressed, 0, sizeof(Keys_pressed));
                return;
            }
        } else {
            long_pressed[index] = 0;
        }

        if (is_pressed){
            Keys_pressed[index] = true;
        } else {
            Keys_pressed[index] = false;
        }
    }
}

void call_buy_UI(bool need_reset){
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
    // release was handled in mouse packet sending
    if (need_reset)
        update_packet_and_send(); 
    // update_packet_and_send(); 
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

// example buy examples:
// #define PA5_BUY_BIG 8
// #define PA4_BUY_BIG 10
// #define PA3_BUY_BIG 12
// #define PA5_SMALL_BIG 80 
// #define PA4_SMALL_BIG 100
// #define PA3_SMALL_BIG 120

// define all coordinates here:
// 1: (1405,860); 2:(1490,860); 5:(1475,860); 10:(1665,860)
// -1: (1150,860); -2: (1070,860); -5: (980,860); -10: (895,860)
// 10: (1405,860); 20:(1490,860); 50:(1475,860); 100:(1665,860)
// -10: (1150,860); -20: (1070,860); -50: (980,860); -100: (895,860)

// steps to buy bullets:
// press O for small bullet, I for big bullets (done)
// move mouse to target position (this depends on calculation)
// left click once (move to position -> press -> unpress)
// press buy button (click buy button)
// press confirm
// press O for small bullet quit and I for big bullet quit

void move_click_mouse(int key){
    // uint16_t x = 0;
    // uint16_t y = 0;

    // switch (key)
    // {
    //     case 0:
    //         if (is_small_bullet){
    //             x = Keys[0].small_bullet_x_pos;
    //             y = Keys[0].small_bullet_y_pos;
    //         } else {
    //             x = Keys[0].big_bullet_x_pos;
    //             y = Keys[0].big_bullet_y_pos;
    //         }
    //         break;
        
    //     case 1:
    //         if (is_small_bullet){
    //             x = Keys[1].small_bullet_x_pos;
    //             y = Keys[1].small_bullet_y_pos;
    //         } else {
    //             x = Keys[1].big_bullet_x_pos;
    //             y = Keys[1].big_bullet_y_pos;
    //         }
    //         break;

    //     case 2:
    //         if (is_small_bullet){
    //             x = Keys[2].small_bullet_x_pos;
    //             y = Keys[2].small_bullet_y_pos;
    //         } else {
    //             x = Keys[2].big_bullet_x_pos;
    //             y = Keys[2].big_bullet_y_pos;
    //         }
    //         break;
        
    //     default:
    //         break;
    // }

    // if (x == 0 && y == 0){
    //     // Position not filled yet. Do not click the top-left corner by accident.
    //     return;
    // }

    // mouse_control(x, y, false);
    // mouse_control(x, y, true);
    // mouse_control(x, y, false);
    
    uint32_t amount = 0;

    // start inot logic:
    switch (key)
    {
    case 0: // PA3
        amount = is_small_bullet ? PA3_BUY_SMALL : PA3_BUY_BIG;
        break;
    
    case 1:
        amount = is_small_bullet ? PA4_BUY_SMALL : PA4_BUY_BIG;
        break;

    case 2:
        amount = is_small_bullet ? PA5_BUY_SMALL : PA5_BUY_BIG;
        break;
        
    default:
        break;
    }

    get_key_pattern(amount);

}

void click_buy_bullets(){

    mouse_control(BUY_BUTTON_X, BUY_BUTTON_Y, false);
    mouse_control(BUY_BUTTON_X, BUY_BUTTON_Y, true);
    mouse_control(BUY_BUTTON_X, BUY_BUTTON_Y, false);

    mouse_control(CONFIRM_BUTTON_X, CONFIRM_BUTTON_Y, false);
    mouse_control(CONFIRM_BUTTON_X, CONFIRM_BUTTON_Y, true);
    mouse_control(CONFIRM_BUTTON_X, CONFIRM_BUTTON_Y, false);
    
    // also quit the page
    call_buy_UI(1); // click the corresponding key again to close UI
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
