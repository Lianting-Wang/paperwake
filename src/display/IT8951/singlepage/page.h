#ifndef SINGLEPAGE_PAGE_H
#define SINGLEPAGE_PAGE_H

#include "../lib/e-Paper/EPD_IT8951.h"

UBYTE SinglePage_Show(UWORD panel_width, UWORD panel_height, UDOUBLE target_memory_addr, bool four_byte_align, int display_mode);

#endif
