#include "page.h"
#include "page_render.h"
#include "../lib/Config/Debug.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

extern char **environ;

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
    if(!Page_ValidText(title, 40, false) || !Page_ValidText(status, 24, false)) return false;
    content->title = title;
    content->status = status;
    for(unsigned int i = 0; i < PAGE_ROWS; i++) {
        if(labels[i] == NULL && values[i] == NULL) continue;
        if(!Page_ValidText(labels[i], 20, false) || !Page_ValidText(values[i], 48, true)) return false;
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

UBYTE SinglePage_Show(UWORD panel_width, UWORD panel_height, UDOUBLE target_memory_addr, bool four_byte_align, int display_mode)
{
    PageContent content;
    UWORD render_width = four_byte_align ? (panel_width - (panel_width % 32)) : panel_width;
    size_t image_size = (size_t)(render_width / 2) * panel_height;
    char current_time[32], battery_value[32];
    if(panel_width != 1872 || panel_height != 1404 || !ReadContent(&content)) {
        Debug("Invalid panel geometry or page content\r\n");
        return 1;
    }
    UBYTE *frame_buffer = malloc(image_size);
    if(frame_buffer == NULL) {
        Debug("Failed to allocate single page frame buffer\r\n");
        return 1;
    }
    FormatCurrentTime(current_time, sizeof(current_time));
    if(!ReadBatteryValue(battery_value, sizeof(battery_value))) snprintf(battery_value, sizeof(battery_value), "N/A");
    if(Page_Render(frame_buffer, image_size, render_width, panel_height, display_mode,
                   &content, current_time, battery_value) != 0) {
        free(frame_buffer);
        return 1;
    }
    EPD_IT8951_4bp_Refresh(frame_buffer, 0, 0, render_width, panel_height, false, target_memory_addr, false);
    free(frame_buffer);
    return EPD_IT8951_HasError() ? 1 : 0;
}
