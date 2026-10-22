#include "../lib/Config/DEV_Config.h"
#include "page.h"

#include <signal.h>
#include <stdlib.h>
#include <string.h>

static const UWORD VCOM_MV = 1560;
static const int DISPLAY_MODE = 1;

static IT8951_Dev_Info Dev_Info = {0};
static bool Module_Initialized = false;

static bool SinglePage_UsesFourByteAlign(const char *lut_version)
{
    bool four_byte_align = false;

    if(strcmp(lut_version, "M641") == 0) {
        A2_Mode = 4;
        four_byte_align = true;
    } else if(strcmp(lut_version, "M841_TFAB512") == 0) {
        A2_Mode = 6;
        four_byte_align = true;
    } else if(strcmp(lut_version, "M841") == 0) {
        A2_Mode = 6;
    } else if(strcmp(lut_version, "M841_TFA2812") == 0) {
        A2_Mode = 6;
    } else if(strcmp(lut_version, "M841_TFA5210") == 0) {
        A2_Mode = 6;
    } else {
        A2_Mode = 6;
    }

    return four_byte_align;
}

static void Handler(int signo)
{
    (void)signo;

    Debug("\r\nHandler:exit\r\n");
    if(Dev_Info.Panel_W != 0) {
        Debug("Going to sleep\r\n");
        EPD_IT8951_Sleep();
    }
    if(Module_Initialized) {
        DEV_Module_Exit();
    }
    exit(0);
}

int main(void)
{
    UWORD panel_width;
    UWORD panel_height;
    UDOUBLE target_memory_addr;
    bool four_byte_align;
    int result;
    char *lut_version;

    signal(SIGINT, Handler);

    if(DEV_Module_Init() != 0) {
        return -1;
    }
    Module_Initialized = true;

    Debug("VCOM value:%d\r\n", VCOM_MV);
    Debug("Display mode:%d\r\n", DISPLAY_MODE);

    Dev_Info = EPD_IT8951_Init(VCOM_MV);
    if(Dev_Info.Panel_W == 0 || Dev_Info.Panel_H == 0) {
        Debug("Failed to read panel information\r\n");
        DEV_Module_Exit();
        Module_Initialized = false;
        return -1;
    }

    panel_width = Dev_Info.Panel_W;
    panel_height = Dev_Info.Panel_H;
    target_memory_addr = Dev_Info.Memory_Addr_L | (Dev_Info.Memory_Addr_H << 16);
    lut_version = (char *)Dev_Info.LUT_Version;
    four_byte_align = SinglePage_UsesFourByteAlign(lut_version);

    Debug("A2 Mode:%d\r\n", A2_Mode);

    EPD_IT8951_Clear_Refresh(Dev_Info, target_memory_addr, INIT_Mode);

    result = SinglePage_Show(panel_width, panel_height, target_memory_addr, four_byte_align, DISPLAY_MODE);
    if(result != 0) {
        Debug("Single page render failed\r\n");
    }

    EPD_IT8951_Sleep();
    DEV_Delay_ms(5000);
    DEV_Module_Exit();
    Module_Initialized = false;

    return (result == 0) ? 0 : -1;
}
