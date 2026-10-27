#ifndef SINGLEPAGE_PAGE_H
#define SINGLEPAGE_PAGE_H

#include "../lib/e-Paper/EPD_IT8951.h"

/* Validate all page environment variables before initializing/clearing hardware. */
UBYTE SinglePage_ValidateContent(void);
UBYTE SinglePage_Show(IT8951_Dev_Info info, UDOUBLE target_memory_addr, bool four_byte_align, int display_mode);

#endif
