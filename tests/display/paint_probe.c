#include "singlepage/page.h"
#include "singlepage/page_render.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
UBYTE INIT_Mode=0;
static int captures,clears;
static UWORD capture_width;
static UBYTE captured[1872/2*1404];
bool EPD_IT8951_HasError(void) { return false; }
void EPD_IT8951_Clear_Refresh(IT8951_Dev_Info info,UDOUBLE address,UWORD mode) {
    assert(info.Panel_W==1872 && address==0 && mode==INIT_Mode); clears++;
}
void EPD_IT8951_4bp_Refresh(UBYTE *buf,UWORD x,UWORD y,UWORD w,UWORD h,bool hold,UDOUBLE addr,bool packed) {
    assert(x==0 && y==0 && !hold && addr==0 && !packed && h==1404);
    assert(w==1872 || w==1856); capture_width=w; memcpy(captured,buf,w/2*h); captures++;
}
static unsigned pixel(const UBYTE *buf,unsigned width,unsigned x,unsigned y) {
    unsigned byte=buf[y*(width/2)+x/2]; return x%2?byte>>4:byte&15;
}
int main(int argc,char **argv) {
    assert(argc==3); setenv("PISCHED_PAGE_FILE",argv[1],1); setenv("PISCHED_FONT_DIR",argv[2],1);
    FILE *file=fopen(argv[1],"rb"); assert(file); char json[65536]; size_t length=fread(json,1,sizeof(json),file); fclose(file);
    assert(SinglePage_ValidateContent()==0);
    IT8951_Dev_Info info={0}; info.Panel_W=1872; info.Panel_H=1404;
    UBYTE *plain=malloc(sizeof(captured)); assert(plain); char issues[8192];
    for(int align=0;align<2;align++) {
        unsigned width=align?1856:1872;
        assert(Page_RenderDocument(plain,sizeof(captured),width,1404,0,json,length,argv[2],issues,sizeof(issues),0)==0);
        assert(!strcmp(issues,"[]"));
        assert(SinglePage_Show(info,0,align,1)==0 && capture_width==width);
        for(unsigned y=0;y<1404;y++) for(unsigned x=0;x<width;x++)
            assert(pixel(plain,width,x,y)==pixel(captured,width,width-x-1,y));
        assert(pixel(plain,width,10,10)==15);
        assert(pixel(plain,width,100,156)==0);
    }
    int before=captures,clear_before=clears;
    setenv("PISCHED_PAGE_FILE","/does/not/exist",1);
    assert(SinglePage_ValidateContent()!=0 && SinglePage_Show(info,0,false,0)!=0);
    setenv("PISCHED_PAGE_FILE",argv[1],1); setenv("PISCHED_FONT_DIR","/missing/fonts",1);
    assert(SinglePage_ValidateContent()!=0 && SinglePage_Show(info,0,false,0)!=0);
    assert(before==captures && clear_before==clears);
    const char bad[]="{\"title\":\"\xc0\xaf\"}";
    assert(Page_RenderDocument(NULL,0,1872,1404,0,bad,sizeof(bad)-1,argv[2],issues,sizeof(issues),0)!=0);
    assert(Page_RenderDocument(plain,1,1872,1404,0,json,length,argv[2],issues,sizeof(issues),0)!=0);
    free(plain); return 0;
}
