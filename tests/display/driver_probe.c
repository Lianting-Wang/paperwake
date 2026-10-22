#include <assert.h>
#include <time.h>
#include "lib/Config/DEV_Config.h"
static double seconds;
static int busy;
static int writes;
static int fake_clock(clockid_t id, struct timespec *t) {
    (void)id; t->tv_sec = (time_t)seconds++; t->tv_nsec = 0; return 0;
}
#define clock_gettime fake_clock
#include "lib/e-Paper/EPD_IT8951.c"
void DEV_Digital_Write(UWORD pin, UBYTE value) { (void)pin; (void)value; }
UBYTE DEV_Digital_Read(UWORD pin) { (void)pin; return busy; }
void DEV_SPI_WriteByte(UBYTE value) { (void)value; writes++; }
UBYTE DEV_SPI_ReadByte(void) { return 1; }
void DEV_Delay_ms(UDOUBLE ms) { (void)ms; }
void DEV_Delay_us(UDOUBLE us) { (void)us; }
int main(void) {
    busy=0;
    EPD_IT8951_SystemRun();
    assert(EPD_IT8951_HasError());
    assert(writes==0);
    assert(seconds<10);
    Driver_Error=false; seconds=0; busy=1;
    EPD_IT8951_WaitForDisplayReady();
    assert(EPD_IT8951_HasError());
    assert(seconds<40);
    Driver_Error=false;
    EPD_IT8951_Cancel();
    writes=0;
    EPD_IT8951_SystemRun();
    assert(EPD_IT8951_HasError());
    assert(writes==0);
    return 0;
}
