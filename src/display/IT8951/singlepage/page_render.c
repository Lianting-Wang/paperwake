#include "page_render.h"
#include "../lib/json/yyjson.h"
#include <ft2build.h>
#include FT_FREETYPE_H
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef PAGE_RENDER_REVISION
#define PAGE_RENDER_REVISION "device"
#endif
#define MAX_TEXT 1024
#define MAX_ELEMENTS 32
extern UBYTE isColor;
const char *Page_RendererRevision(void) { return PAGE_RENDER_REVISION; }

static const char *string(yyjson_val *v) {
    const char *s=yyjson_get_str(v);
    return s && yyjson_get_len(v)==strlen(s) ? s : NULL;
}
static const char *field(yyjson_val *v,const char *key) { return string(yyjson_obj_get(v,key)); }
static int integer(yyjson_val *v,const char *key,int low,int high,int *out) {
    yyjson_val *n=yyjson_obj_get(v,key);
    if(!yyjson_is_int(n)) return 0;
    int64_t value=yyjson_get_sint(n);
    if(value<low || value>high) return 0;
    *out=(int)value; return 1;
}
static int next_codepoint(const char **p,unsigned *code) {
    const unsigned char *s=(const unsigned char *)*p;
    if(!*s) return 0;
    unsigned n,c,minimum;
    if(*s<0x80) { *code=*s; *p+=1; return 1; }
    if(*s>=0xc2 && *s<=0xdf) { n=1; c=*s&31; minimum=0x80; }
    else if(*s>=0xe0 && *s<=0xef) { n=2; c=*s&15; minimum=0x800; }
    else if(*s>=0xf0 && *s<=0xf4) { n=3; c=*s&7; minimum=0x10000; }
    else return -1;
    for(unsigned i=1;i<=n;i++) {
        if(!s[i] || (s[i]&0xc0)!=0x80) return -1;
        c=(c<<6)|(s[i]&63);
    }
    if(c<minimum || c>0x10ffff || (c>=0xd800 && c<=0xdfff)) return -1;
    *p+=n+1; *code=c; return 1;
}
static int valid_text(const char *s,unsigned limit,int empty,int multiline) {
    if(!s || (!empty && !*s)) return 0;
    unsigned count=0,c; int rc;
    while((rc=next_codepoint(&s,&c))>0) {
        if(++count>limit || (c<32 && !(multiline && c==10)) || (c>=127 && c<=159)) return 0;
    }
    return rc==0;
}
static int identifier(const char *s) {
    if(!s || !*s || strlen(s)>64) return 0;
    for(;*s;s++) if(!((*s>='a' && *s<='z') || (*s>='A' && *s<='Z') ||
                     (*s>='0' && *s<='9') || *s=='-' || *s=='_')) return 0;
    return 1;
}
static const char *bound(yyjson_val *root,const char *binding,char *date) {
    if(!strcmp(binding,"title") || !strcmp(binding,"status") || !strcmp(binding,"clock") || !strcmp(binding,"battery"))
        return field(root,binding);
    if(!strcmp(binding,"date")) {
        const char *clock=field(root,"clock");
        if(clock && strlen(clock)>=10 && clock[4]=='-' && clock[7]=='-') {
            memcpy(date,clock,10); date[10]=0; date[4]=date[7]='.'; return date;
        }
        return clock;
    }
    if(strlen(binding)>=12 && !strncmp(binding,"rows.",5) && binding[5]>='0' && binding[5]<='5' && binding[6]=='.') {
        const char *key=binding+7;
        if(!strcmp(key,"label") || !strcmp(key,"value")) {
            yyjson_val *row=yyjson_arr_get(yyjson_obj_get(root,"rows"),binding[5]-'0');
            return row ? field(row,key) : "";
        }
    }
    return NULL;
}
typedef struct {
    const char *id,*type,*font,*align;
    int x,y,width,height,size,gray,weight;
    double line_height;
    char text[4096];
} Element;

static int read_element(yyjson_val *raw,yyjson_val *root,Element *e,unsigned width) {
    const char *binding=field(raw,"binding"),*text=field(raw,"text");
    const char *prefix=field(raw,"prefix"),*suffix=field(raw,"suffix");
    e->id=field(raw,"id"); e->type=field(raw,"type");
    e->font=field(raw,"font"); e->align=field(raw,"align");
    if(!identifier(e->id) || !e->type || (strcmp(e->type,"text") && strcmp(e->type,"line")) ||
       !e->font || (strcmp(e->font,"sans") && strcmp(e->font,"serif")) ||
       !e->align || (strcmp(e->align,"left") && strcmp(e->align,"center") && strcmp(e->align,"right")) ||
       !binding || !valid_text(text,256,1,1) || !valid_text(prefix,32,1,0) || !valid_text(suffix,32,1,0) ||
       !integer(raw,"x",0,1871,&e->x) || !integer(raw,"y",0,1403,&e->y) ||
       !integer(raw,"width",1,1872,&e->width) || !integer(raw,"height",1,1404,&e->height) ||
       e->x+e->width>1872 || e->y+e->height>1404 ||
       !integer(raw,"size",16,160,&e->size) || !integer(raw,"gray",0,15,&e->gray) ||
       !integer(raw,"weight",400,500,&e->weight) || (e->weight!=400 && e->weight!=500)) return 0;
    yyjson_val *line_height=yyjson_obj_get(raw,"line_height");
    if(!yyjson_is_num(line_height)) return 0;
    e->line_height=yyjson_get_num(line_height);
    if(!isfinite(e->line_height) || e->line_height<1 || e->line_height>2) return 0;
    char date[32];
    const char *value=*binding ? bound(root,binding,date) : text;
    if(!value) return 0;
    if(*binding && !strncmp(binding,"rows.",5) && !*value) e->text[0]=0;
    else if(snprintf(e->text,sizeof(e->text),"%s%s%s",prefix,value,suffix)>=(int)sizeof(e->text)) return 0;
    if(!strcmp(e->type,"line") && !*binding) strcpy(e->text,"line");
    int right=(e->x+e->width)*(int)width/1872;
    e->x=e->x*(int)width/1872; e->width=right-e->x;
    return e->width>0;
}
static int closing(unsigned c) {
    return c==',' || c=='.' || c=='!' || c=='?' || c==':' || c==';' || c==')' || c==']' ||
           c==0x3001 || c==0x3002 || c==0xff0c || c==0xff01 || c==0xff1f || c==0xff1a ||
           c==0xff1b || c==0xff09 || c==0x300b || c==0x201d || c==0x2019;
}
static int draw_text(Element *e,FT_Face face,int drawing) {
    unsigned codes[MAX_TEXT]; int advances[MAX_TEXT],lefts[MAX_TEXT],rights[MAX_TEXT],count=0,missing=0;
    const char *p=e->text;
    if(FT_Set_Pixel_Sizes(face,0,e->size)) return 1;
    while(*p) {
        if(count==MAX_TEXT || next_codepoint(&p,&codes[count])<=0) return 1;
        if(codes[count]==10) advances[count]=lefts[count]=rights[count]=0;
        else {
            FT_UInt index=FT_Get_Char_Index(face,codes[count]);
            if(!index) missing=1;
            if(FT_Load_Glyph(face,index,FT_LOAD_RENDER|FT_LOAD_NO_HINTING)) return 1;
            advances[count]=(int)((face->glyph->advance.x+32)/64);
            lefts[count]=face->glyph->bitmap_left;
            rights[count]=lefts[count]+(int)face->glyph->bitmap.width;
        }
        count++;
    }
    int start=0,line=0,overflow=0,step=(int)ceil(e->size*e->line_height);
    while(start<count) {
        int end=start,used=0,last_space=-1,pad=lefts[start]<0?-lefts[start]:0;
        while(end<count && codes[end]!=10) {
            if(used+advances[end]+pad>e->width || used+rights[end]+pad>e->width) break;
            used+=advances[end]; if(codes[end]==' ') last_space=end; end++;
        }
        if(end==start && codes[start]!=10) { end++; overflow=1; }
        else if(end<count && codes[end]!=10) {
            if(last_space>start) end=last_space;
            else if(closing(codes[end]) && end>start+1) end--;
        }
        used=0; int ink=0;
        for(int i=start;i<end;i++) { if(used+rights[i]>ink) ink=used+rights[i]; used+=advances[i]; }
        int natural=(used>ink?used:ink)+pad;
        int dx=pad+(!strcmp(e->align,"center")?(e->width-natural)/2:!strcmp(e->align,"right")?e->width-natural:0);
        int baseline=e->size+line*step;
        if(baseline>e->height) overflow=1;
        for(int i=start;i<end;i++) {
            FT_UInt index=FT_Get_Char_Index(face,codes[i]);
            if(FT_Load_Glyph(face,index,FT_LOAD_RENDER|FT_LOAD_NO_HINTING)) return 1;
            FT_GlyphSlot g=face->glyph;
            for(unsigned row=0;row<g->bitmap.rows;row++) for(unsigned col=0;col<g->bitmap.width;col++) {
                unsigned char alpha=g->bitmap.buffer[row*g->bitmap.pitch+col];
                if(!alpha) continue;
                int x=dx+g->bitmap_left+(int)col,y=baseline-g->bitmap_top+(int)row;
                if(x<0 || x>=e->width || y<0 || y>=e->height) { overflow=1; continue; }
                int gray=15-(alpha*(15-e->gray)+127)/255;
                if(drawing) Paint_SetPixel(e->x+x,e->y+y,(UWORD)(gray<<4));
            }
            dx+=advances[i];
        }
        line++; start=end;
        while(start<count && codes[start]==' ') start++;
        if(start<count && codes[start]==10) start++;
    }
    return (overflow?1:0)|(missing?2:0);
}

int Page_RenderDocument(UBYTE *frame,size_t capacity,unsigned width,unsigned height,
                        int mode,const char *json,size_t length,const char *font_dir,
                        char *issues,size_t issues_capacity,int draft) {
    int result=1;
    yyjson_doc *doc=NULL;
    yyjson_mut_doc *errors=yyjson_mut_doc_new(NULL);
    if(errors) yyjson_mut_doc_set_root(errors,yyjson_mut_arr(errors));
    FT_Library library=NULL; FT_Face faces[4]={NULL,NULL,NULL,NULL};
    if(issues && issues_capacity) snprintf(issues,issues_capacity,"[]");
    if(!errors || !json || !font_dir || !length || length>65536 ||
       (width!=1872 && width!=1856) || height!=1404 || mode<0 || mode>3 ||
       (frame && capacity<(size_t)width/2*height)) goto done;
    doc=yyjson_read(json,length,0); if(!doc) goto done;
    yyjson_val *root=yyjson_doc_get_root(doc),*rows=yyjson_obj_get(root,"rows");
    if(!yyjson_is_obj(root) || !valid_text(field(root,"title"),40,draft,0) ||
       !valid_text(field(root,"status"),24,draft,0) || !yyjson_is_arr(rows) || yyjson_arr_size(rows)>6 ||
       !valid_text(field(root,"clock"),63,0,0) || !valid_text(field(root,"battery"),31,0,0)) goto done;
    size_t i,max; yyjson_val *value;
    yyjson_arr_foreach(rows,i,max,value) {
        if(!valid_text(field(value,"label"),20,draft,0) || !valid_text(field(value,"value"),48,1,1)) goto done;
    }
    yyjson_val *layout=yyjson_obj_get(root,"layout"),*items=yyjson_obj_get(layout,"elements");
    int version,revision;
    if(!identifier(field(layout,"id")) || !integer(layout,"schema_version",1,1,&version) ||
       !integer(layout,"revision",1,2147483647,&revision) || !yyjson_is_arr(items) ||
       yyjson_arr_size(items)<1 || yyjson_arr_size(items)>MAX_ELEMENTS || FT_Init_FreeType(&library)) goto done;
    Element elements[MAX_ELEMENTS];
    yyjson_arr_foreach(items,i,max,value) {
        if(!read_element(value,root,&elements[i],width)) goto done;
        for(size_t j=0;j<i;j++) if(!strcmp(elements[j].id,elements[i].id)) goto done;
    }
    if(frame) {
        Paint_NewImage(frame,width,height,0,0); Paint_SelectImage(frame); isColor=mode==3;
        Paint_SetRotate(ROTATE_0); Paint_SetMirroring((mode==1 || mode==2)?MIRROR_HORIZONTAL:MIRROR_NONE);
        Paint_SetBitsPerPixel(4); Paint_Clear(0xf0);
    }
    result=0;
    for(i=0;i<yyjson_arr_size(items);i++) {
        Element *e=&elements[i]; if(!*e->text) continue;
        if(!strcmp(e->type,"line")) {
            if(frame) for(int y=e->y;y<e->y+e->height;y++) for(int x=e->x;x<e->x+e->width;x++)
                Paint_SetPixel(x,y,(UWORD)(e->gray<<4));
            continue;
        }
        int f=(!strcmp(e->font,"serif")?2:0)+(e->weight==500);
        if(!faces[f]) {
            char path[4096];
            if(snprintf(path,sizeof(path),"%s/Noto%sSC-%s.otf",font_dir,f>=2?"Serif":"Sans",f%2?"Medium":"Regular")>=(int)sizeof(path) ||
               FT_New_Face(library,path,0,&faces[f])) { result=1; goto done; }
        }
        int problem=draw_text(e,faces[f],frame!=NULL);
        if(problem) {
            result=2; yyjson_mut_val *error=yyjson_mut_obj(errors);
            yyjson_mut_obj_add_str(errors,error,"element_id",e->id);
            yyjson_mut_obj_add_str(errors,error,"code",problem&2?"missing_glyph":"overflow");
            yyjson_mut_arr_append(yyjson_mut_doc_get_root(errors),error);
        }
    }
done:
    if(errors && issues && issues_capacity) {
        char *encoded=yyjson_mut_write(errors,0,NULL);
        if(encoded) { snprintf(issues,issues_capacity,"%s",encoded); free(encoded); }
    }
    for(int j=0;j<4;j++) if(faces[j]) FT_Done_Face(faces[j]);
    if(library) FT_Done_FreeType(library);
    yyjson_doc_free(doc); yyjson_mut_doc_free(errors); return result;
}
