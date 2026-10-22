#include "singlepage/page.h"
#include "lib/GUI/GUI_Paint.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

static int captures;
bool EPD_IT8951_HasError(void) { return false; }
void EPD_IT8951_4bp_Refresh(UBYTE *buf, UWORD x, UWORD y, UWORD w, UWORD h, bool hold, UDOUBLE addr, bool packed) {
    (void)x; (void)y; (void)hold; (void)addr; (void)packed;
    assert(w == 1872 && h == 1404);
    assert(buf[700 * (w/2) + 5] == 0xff);
    captures++;
}
int main(void) {
    UBYTE *buf = malloc(8);
    Paint_NewImage(buf, 4, 4, 0, 0);
    Paint_SetBitsPerPixel(4);
    Paint_Clear(0xf0);
    for(int i=0; i<8; i++) assert(buf[i] == 0xff);
    Paint_SetPixel(0,4,0);  /* ASan must remain clean at the formerly unsafe boundary. */
    Paint_SetPixel(4,4,0);
    for(int i=0; i<8; i++) assert(buf[i] == 0xff);
    Paint_SetPixel(3,3,0);
    unsigned black=0;
    for(int i=0; i<8; i++) { black += ((buf[i] & 15) == 0); black += ((buf[i] >> 4) == 0); }
    assert(black == 1);
    free(buf);
    assert(SinglePage_Show(1872,1404,0,false,1) == 0);
    assert(captures == 1);
    return 0;
}
