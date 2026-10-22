#include "page.h"

#include "../lib/Config/Debug.h"
#include "../lib/GUI/GUI_Paint.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define PAGE_WHITE      0xF0
#define PAGE_BLACK      0x00
#define PAGE_MID_GRAY   0xC0
#define PAGE_LIGHT_GRAY 0xD0
#define PAGE_DARK_GRAY  0x80
#define PAGE_ROWS       6
#define VALUE_COLUMNS   40

extern UBYTE isColor;
extern char **environ;

typedef struct {
    const char *title;
    const char *status;
    const char *labels[PAGE_ROWS];
    const char *values[PAGE_ROWS];
    unsigned int row_count;
} PageContent;

static bool ValidText(const char *value, size_t limit, bool allow_empty)
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

static bool ReadContent(PageContent *content)
{
    const char *title = getenv("PISCHED_PAGE_TITLE");
    const char *status = getenv("PISCHED_PAGE_STATUS");
    bool supplied = title != NULL || status != NULL;
    char key[32];
    const char *labels[PAGE_ROWS];
    const char *values[PAGE_ROWS];

    /* Reject unsupported row indices instead of silently dropping a seventh row. */
    for(char **entry = environ; entry != NULL && *entry != NULL; entry++) {
        const char *index = NULL;
        if(strncmp(*entry, "PISCHED_PAGE_LABEL_", 19) == 0) index = *entry + 19;
        if(strncmp(*entry, "PISCHED_PAGE_VALUE_", 19) == 0) index = *entry + 19;
        if(index != NULL && !(index[0] >= '1' && index[0] <= '6' && index[1] == '=')) return false;
    }
    for(unsigned int i = 0; i < PAGE_ROWS; i++) {
        snprintf(key, sizeof(key), "PISCHED_PAGE_LABEL_%u", i + 1);
        labels[i] = getenv(key);
        snprintf(key, sizeof(key), "PISCHED_PAGE_VALUE_%u", i + 1);
        values[i] = getenv(key);
        supplied = supplied || labels[i] != NULL || values[i] != NULL;
    }
    memset(content, 0, sizeof(*content));
    if(!supplied) {
        content->title = "PI STATUS";
        content->status = "SNAPSHOT";
        content->labels[0] = "DEVICE";
        content->values[0] = "PI ZERO2";
        content->labels[1] = "PANEL";
        content->values[1] = "1872x1404";
        content->row_count = 2;
        return true;
    }
    if(!ValidText(title, 40, false) || !ValidText(status, 24, false)) return false;
    content->title = title;
    content->status = status;
    for(unsigned int i = 0; i < PAGE_ROWS; i++) {
        if(labels[i] == NULL && values[i] == NULL) continue;
        if(!ValidText(labels[i], 20, false) || !ValidText(values[i], 48, true)) return false;
        content->labels[content->row_count] = labels[i];
        content->values[content->row_count] = values[i];
        content->row_count++;
    }
    return true;
}

UBYTE SinglePage_ValidateContent(void)
{
    PageContent content;
    if(ReadContent(&content)) return 0;
    Debug("Invalid page content; display left unchanged\r\n");
    return 1;
}

static void FillBox(UWORD x0, UWORD y0, UWORD x1, UWORD y1, UWORD fill_color)
{
    Paint_DrawRectangle(x0, y0, x1, y1, fill_color, DOT_PIXEL_1X1, DRAW_FILL_FULL);
    Paint_DrawRectangle(x0, y0, x1, y1, PAGE_BLACK, DOT_PIXEL_1X1, DRAW_FILL_EMPTY);
}

static bool ReadBatteryValue(char *buffer, size_t buffer_size)
{
    const char *value = getenv("PISCHED_BATTERY");
    char *end;
    double percent;
    if(value == NULL || *value == 0) return false;
    percent = strtod(value, &end);
    if(*end != 0 || !isfinite(percent) || percent < 0 || percent > 100) return false;
    snprintf(buffer, buffer_size, "%.1f%%", percent);
    return true;
}

static void FormatCurrentTime(char *buffer, size_t buffer_size)
{
    time_t now = time(NULL);
    struct tm local_tm;
    if(localtime_r(&now, &local_tm) == NULL) {
        snprintf(buffer, buffer_size, "N/A");
        return;
    }
    strftime(buffer, buffer_size, "%Y-%m-%d %H:%M", &local_tm);
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

UBYTE SinglePage_Show(UWORD panel_width, UWORD panel_height, UDOUBLE target_memory_addr, bool four_byte_align, int display_mode)
{
    PageContent content;
    UWORD render_width = four_byte_align ? (panel_width - (panel_width % 32)) : panel_width;
    const UWORD margin = 50;
    const UWORD header_height = 180;
    const UWORD content_top = 230;
    const UWORD content_bottom = 1244;
    const UWORD footer_y = 1294;
    UWORD right_width;
    UWORD left_width;
    UWORD right_x;
    UDOUBLE image_size;
    UBYTE *frame_buffer;
    char current_time[32];
    char battery_value[32];

    /* This page is designed for the installed 10.3-inch panel, including its
       optional 32-pixel transfer alignment. Reject geometry before drawing. */
    if(panel_width != 1872 || panel_height != 1404 || !ReadContent(&content)) {
        Debug("Invalid panel geometry or page content\r\n");
        return 1;
    }
    image_size = (UDOUBLE)(render_width / 2) * panel_height;
    frame_buffer = (UBYTE *)malloc(image_size);
    if(frame_buffer == NULL) {
        Debug("Failed to allocate single page frame buffer\r\n");
        return 1;
    }

    Paint_NewImage(frame_buffer, render_width, panel_height, 0, PAGE_BLACK);
    Paint_SelectImage(frame_buffer);
    SinglePage_SetDisplayMode(display_mode);
    Paint_SetBitsPerPixel(4);
    Paint_Clear(PAGE_WHITE);
    FormatCurrentTime(current_time, sizeof(current_time));
    if(!ReadBatteryValue(battery_value, sizeof(battery_value))) snprintf(battery_value, sizeof(battery_value), "N/A");

    right_width = (render_width - 2 * margin) / 3;
    left_width = render_width - 3 * margin - right_width;
    right_x = 2 * margin + left_width;

    FillBox(0, 0, render_width - 1, header_height - 1, PAGE_MID_GRAY);
    Paint_DrawString_EN(margin, 68, content.title, &Font20, PAGE_BLACK, PAGE_MID_GRAY);
    Paint_DrawString_EN(margin, 110, "DEVICE SNAPSHOT", &Font16, PAGE_BLACK, PAGE_MID_GRAY);
    Paint_DrawLine(0, header_height, render_width - 1, header_height, PAGE_BLACK, DOT_PIXEL_1X1, LINE_STYLE_SOLID);

    FillBox(margin, content_top, margin + left_width - 1, content_bottom - 1, PAGE_WHITE);
    for(unsigned int i = 0; i < content.row_count; i++) {
        UWORD y = content_top + 40 + i * 155;
        Paint_DrawString_EN(margin + 40, y, content.labels[i], &Font16, PAGE_BLACK, PAGE_WHITE);
        DrawValue(margin + 40, y + 28, content.values[i]);
    }

    DrawInfoCard(right_x, content_top, render_width - margin - 1, 551, "TIME", current_time);
    DrawInfoCard(right_x, 576, render_width - margin - 1, 897, "BATTERY", battery_value);
    DrawInfoCard(right_x, 922, render_width - margin - 1, content_bottom - 1, "STATUS", content.status);

    Paint_DrawLine(margin, footer_y, render_width - margin, footer_y, PAGE_BLACK, DOT_PIXEL_1X1, LINE_STYLE_SOLID);
    Paint_DrawString_EN(margin, footer_y + 25, "PI STATUS", &Font16, PAGE_BLACK, PAGE_WHITE);
    EPD_IT8951_4bp_Refresh(frame_buffer, 0, 0, render_width, panel_height, false, target_memory_addr, false);
    free(frame_buffer);
    return EPD_IT8951_HasError() ? 1 : 0;
}
