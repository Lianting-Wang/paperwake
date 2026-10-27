#include "page.h"
#include "page_render.h"
#include "../lib/Config/Debug.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *font_directory(void) {
    const char *path=getenv("PISCHED_FONT_DIR");
    return path ? path : "/home/wlt/IT8951/assets/fonts";
}
static char *read_document(size_t *length) {
    const char *path=getenv("PISCHED_PAGE_FILE");
    if(!path || path[0]!='/') return NULL;
    FILE *file=fopen(path,"rb");
    if(!file) return NULL;
    char *data=malloc(65537);
    if(!data) { fclose(file); return NULL; }
    *length=fread(data,1,65537,file);
    int invalid=ferror(file) || *length==0 || *length>65536;
    fclose(file);
    if(invalid) { free(data); return NULL; }
    return data;
}
UBYTE SinglePage_ValidateContent(void) {
    size_t length=0; char *json=read_document(&length),issues[8192];
    if(!json) return 1;
    int rc=Page_RenderDocument(NULL,0,1872,1404,0,json,length,font_directory(),issues,sizeof(issues),0);
    if(!rc) rc=Page_RenderDocument(NULL,0,1856,1404,0,json,length,font_directory(),issues,sizeof(issues),0);
    if(rc) Debug("Invalid page document; display left unchanged: %s\r\n",issues);
    free(json); return rc?1:0;
}
UBYTE SinglePage_Show(IT8951_Dev_Info info,UDOUBLE target_memory_addr,bool four_byte_align,int display_mode) {
    if(info.Panel_W!=1872 || info.Panel_H!=1404) return 1;
    UWORD width=four_byte_align ? info.Panel_W-(info.Panel_W%32) : info.Panel_W;
    size_t image_size=(size_t)width/2*info.Panel_H,length=0;
    char *json=read_document(&length),issues[8192];
    UBYTE *frame=malloc(image_size);
    if(!json || !frame) { free(json); free(frame); return 1; }
    int rc=Page_RenderDocument(frame,image_size,width,info.Panel_H,display_mode,json,length,
                               font_directory(),issues,sizeof(issues),0);
    if(!rc) {
        /* Only clear once a complete, valid frame is available. */
        EPD_IT8951_Clear_Refresh(info,target_memory_addr,INIT_Mode);
        if(EPD_IT8951_HasError()) rc=1;
        else EPD_IT8951_4bp_Refresh(frame,0,0,width,info.Panel_H,false,target_memory_addr,false);
    } else Debug("Page render rejected: %s\r\n",issues);
    free(json); free(frame);
    return rc || EPD_IT8951_HasError() ? 1 : 0;
}
