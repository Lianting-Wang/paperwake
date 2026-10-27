#include "page_render.h"

#include <string.h>

#define PAGE_WHITE      0xF0
#define PAGE_BLACK      0x00
#define PAGE_MID_GRAY   0xC0
#define PAGE_LIGHT_GRAY 0xD0
#define PAGE_DARK_GRAY  0x80
#define VALUE_COLUMNS   40

#ifndef PAGE_RENDER_REVISION
#define PAGE_RENDER_REVISION "device"
#endif
extern UBYTE isColor;

const char *Page_RendererRevision(void) { return PAGE_RENDER_REVISION; }

bool Page_ValidText(const char *value, size_t limit, bool allow_empty)
{
    size_t length;
    if(value == NULL) return false;
    length = strnlen(value, limit + 1);
    if(length > limit || (!allow_empty && length == 0)) return false;
    for(size_t i = 0; i < length; i++) {
        if((unsigned char)value[i] < 0x20 || (unsigned char)value[i] > 0x7e) return false;
    }
    return true;
}

static void FillBox(UWORD x0, UWORD y0, UWORD x1, UWORD y1, UWORD fill_color)
{
    Paint_DrawRectangle(x0, y0, x1, y1, fill_color, DOT_PIXEL_1X1, DRAW_FILL_FULL);
    Paint_DrawRectangle(x0, y0, x1, y1, PAGE_BLACK, DOT_PIXEL_1X1, DRAW_FILL_EMPTY);
}

static void DrawInfoCard(UWORD x0, UWORD y0, UWORD x1, UWORD y1, const char *title, const char *value)
{
    const UWORD padding = 40;
    FillBox(x0, y0, x1, y1, PAGE_LIGHT_GRAY);
    Paint_DrawString_EN(x0 + padding, y0 + padding, title, &Font16, PAGE_BLACK, PAGE_LIGHT_GRAY);
    Paint_DrawLine(x0 + padding, y0 + 70, x1 - padding, y0 + 70, PAGE_DARK_GRAY, DOT_PIXEL_1X1, LINE_STYLE_SOLID);
    Paint_DrawString_EN(x0 + padding, y0 + 100, value, &Font20, PAGE_BLACK, PAGE_LIGHT_GRAY);
}

static void DrawValue(UWORD x, UWORD y, const char *value)
{
    char line[VALUE_COLUMNS + 1];
    size_t remaining = strlen(value);
    /* Explicit wrapping stays inside each row. Paint's automatic wrapping uses
       the entire panel and can otherwise overwrite another region. */
    while(remaining > 0) {
        size_t length = remaining > VALUE_COLUMNS ? VALUE_COLUMNS : remaining;
        memcpy(line, value, length);
        line[length] = 0;
        Paint_DrawString_EN(x, y, line, &Font20, PAGE_BLACK, PAGE_WHITE);
        value += length;
        remaining -= length;
        y += Font20.Height + 6;
    }
}

static void SinglePage_SetDisplayMode(int mode)
{
    isColor = 0;
    Paint_SetRotate(ROTATE_0);
    Paint_SetMirroring((mode == 1 || mode == 2) ? MIRROR_HORIZONTAL : MIRROR_NONE);
    if(mode == 3) isColor = 1;
}

int Page_Render(UBYTE *frame_buffer, size_t capacity, unsigned int render_width,
                unsigned int panel_height, int display_mode, const PageContent *content,
                const char *current_time, const char *battery_value)
{
    const UWORD margin = 50;
    const UWORD header_height = 180;
    const UWORD content_top = 230;
    const UWORD content_bottom = 1244;
    const UWORD footer_y = 1294;
    UWORD right_width, left_width, right_x;
    if(frame_buffer == NULL || content == NULL || (render_width != 1872 && render_width != 1856)
       || panel_height != 1404 || capacity < (size_t)render_width / 2 * panel_height
       || display_mode < 0 || display_mode > 3 || content->row_count > PAGE_ROWS
       || !Page_ValidText(content->title, 40, true) || !Page_ValidText(content->status, 24, true)
       || !Page_ValidText(current_time, 31, false) || !Page_ValidText(battery_value, 31, false)) return 1;
    for(unsigned int i = 0; i < content->row_count; i++) {
        if(!Page_ValidText(content->labels[i], 20, true) || !Page_ValidText(content->values[i], 48, true)) return 1;
    }
    Paint_NewImage(frame_buffer, render_width, panel_height, 0, PAGE_BLACK);
    Paint_SelectImage(frame_buffer);
    SinglePage_SetDisplayMode(display_mode);
    Paint_SetBitsPerPixel(4);
    Paint_Clear(PAGE_WHITE);
    right_width = (render_width - 2 * margin) / 3;
    left_width = render_width - 3 * margin - right_width;
    right_x = 2 * margin + left_width;

    FillBox(0, 0, render_width - 1, header_height - 1, PAGE_MID_GRAY);
    Paint_DrawString_EN(margin, 68, content->title, &Font20, PAGE_BLACK, PAGE_MID_GRAY);
    Paint_DrawString_EN(margin, 110, "DEVICE SNAPSHOT", &Font16, PAGE_BLACK, PAGE_MID_GRAY);
    Paint_DrawLine(0, header_height, render_width - 1, header_height, PAGE_BLACK, DOT_PIXEL_1X1, LINE_STYLE_SOLID);

    FillBox(margin, content_top, margin + left_width - 1, content_bottom - 1, PAGE_WHITE);
    for(unsigned int i = 0; i < content->row_count; i++) {
        UWORD y = content_top + 40 + i * 155;
        Paint_DrawString_EN(margin + 40, y, content->labels[i], &Font16, PAGE_BLACK, PAGE_WHITE);
        DrawValue(margin + 40, y + 28, content->values[i]);
    }

    DrawInfoCard(right_x, content_top, render_width - margin - 1, 551, "TIME", current_time);
    DrawInfoCard(right_x, 576, render_width - margin - 1, 897, "BATTERY", battery_value);
    DrawInfoCard(right_x, 922, render_width - margin - 1, content_bottom - 1, "STATUS", content->status);

    Paint_DrawLine(margin, footer_y, render_width - margin, footer_y, PAGE_BLACK, DOT_PIXEL_1X1, LINE_STYLE_SOLID);
    Paint_DrawString_EN(margin, footer_y + 25, "PI STATUS", &Font16, PAGE_BLACK, PAGE_WHITE);
    return 0;
}
