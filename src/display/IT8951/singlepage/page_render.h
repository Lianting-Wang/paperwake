#ifndef SINGLEPAGE_RENDER_H
#define SINGLEPAGE_RENDER_H
#include <stddef.h>
#include "../lib/GUI/GUI_Paint.h"
/* NULL frame measures only. Results: 0 valid, 1 invalid input/resources,
 * 2 text overflow/missing glyphs. Drafts are clipped; hardware submits only 0.
 * Callers serialize Paint's global state. */
int Page_RenderDocument(UBYTE *frame, size_t capacity, unsigned width, unsigned height,
                        int mode, const char *json, size_t length, const char *font_dir,
                        char *issues, size_t issues_capacity, int draft);
const char *Page_RendererRevision(void);
#endif
