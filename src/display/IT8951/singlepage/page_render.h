#ifndef SINGLEPAGE_RENDER_H
#define SINGLEPAGE_RENDER_H

#include <stddef.h>
#include <stdbool.h>
#include "../lib/GUI/GUI_Paint.h"

#define PAGE_ROWS 6

typedef struct {
    const char *title;
    const char *status;
    const char *labels[PAGE_ROWS];
    const char *values[PAGE_ROWS];
    unsigned int row_count;
} PageContent;

bool Page_ValidText(const char *value, size_t limit, bool allow_empty);
/* Draw only. No environment, clock, SPI, GPIO or display refresh is accessed.
   Paint uses global state; callers must serialize calls within each process. */
int Page_Render(UBYTE *frame_buffer, size_t capacity, unsigned int render_width,
                unsigned int panel_height, int display_mode, const PageContent *content,
                const char *current_time, const char *battery_value);
const char *Page_RendererRevision(void);

#endif
