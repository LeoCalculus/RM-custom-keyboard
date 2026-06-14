#ifndef __UTILS_H
#define __UTILS_H

#include <stdint.h>
#include <stddef.h>

#define CRC8_INIT (0xff)
#define CRC16_INIT (0xffff)
unsigned char Get_CRC8_Check_Sum(unsigned char *pchMessage, unsigned int dwLength, unsigned char ucCRC8);
uint16_t Get_CRC16_Check_Sum(const uint8_t *pchMessage, uint32_t dwLength, uint16_t wCRC);


#endif
