#include "singlepage/page.h"
#include "lib/GUI/GUI_Paint.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PANEL_WIDTH 1872
#define PANEL_HEIGHT 1404
static int captures;
static UWORD capture_width;
static UBYTE captured[PANEL_WIDTH / 2 * PANEL_HEIGHT];

bool EPD_IT8951_HasError(void) { return false; }
void EPD_IT8951_4bp_Refresh(UBYTE *buf, UWORD x, UWORD y, UWORD w, UWORD h, bool hold, UDOUBLE addr, bool packed)
{
    assert(x == 0 && y == 0 && !hold && !packed && addr == 0);
    assert((w == PANEL_WIDTH || w == 1856) && h == PANEL_HEIGHT);
    capture_width = w;
    memcpy(captured, buf, w / 2 * h);
    captures++;
}

static unsigned pixel(unsigned x, unsigned y)
{
    assert(x < capture_width && y < PANEL_HEIGHT);
    unsigned byte = captured[y * (capture_width / 2) + x / 2];
    return x % 2 ? byte >> 4 : byte & 15;
}

static void save_preview(void)
{
    const char *path = getenv("EPD_PREVIEW_PATH");
    if(path == NULL || *path == 0) return;
    FILE *file = fopen(path, "wb");
    assert(file != NULL);
    fprintf(file, "P5\n%u %u\n255\n", capture_width, PANEL_HEIGHT);
    for(unsigned y = 0; y < PANEL_HEIGHT; y++) {
        for(unsigned x = 0; x < capture_width; x++) assert(fputc(pixel(x, y) * 17, file) != EOF);
    }
    assert(fclose(file) == 0);
}

/* Check complete glyph rectangles in the frame actually sent to the driver,
   independently of Paint_DrawString's wrapping and mirroring. */
static void text_at(unsigned x, unsigned y, const char *text, const sFONT *font, unsigned background, bool mirror)
{
    unsigned row_bytes = (font->Width + 7) / 8;
    for(unsigned i = 0; text[i] != 0; i++) {
        const UBYTE *glyph = font->table + (text[i] - ' ') * font->Height * row_bytes;
        for(unsigned row = 0; row < font->Height; row++) {
            for(unsigned col = 0; col < font->Width; col++) {
                unsigned expected = glyph[row * row_bytes + col / 8] & (0x80 >> (col % 8)) ? 0 : background;
                unsigned px = x + i * font->Width + col;
                if(mirror) px = capture_width - px - 1;
                if(pixel(px, y + row) != expected) fprintf(stderr, "text '%s' at %u,%u: pixel %u,%u got %u expected %u\n", text, x, y, px, y + row, pixel(px, y + row), expected);
                assert(pixel(px, y + row) == expected);
            }
        }
    }
}

static void clear_content(void)
{
    char key[32];
    unsetenv("PISCHED_PAGE_TITLE");
    unsetenv("PISCHED_PAGE_STATUS");
    for(unsigned i = 0; i <= 7; i++) {
        snprintf(key, sizeof(key), "PISCHED_PAGE_LABEL_%u", i);
        unsetenv(key);
        snprintf(key, sizeof(key), "PISCHED_PAGE_VALUE_%u", i);
        unsetenv(key);
    }
}

static void rejected(void)
{
    int previous = captures;
    assert(SinglePage_ValidateContent() != 0);
    assert(SinglePage_Show(PANEL_WIDTH, PANEL_HEIGHT, 0, false, 0) != 0);
    assert(captures == previous);
}

static void pixel_boundaries(void)
{
    UBYTE *buf = malloc(8);
    Paint_NewImage(buf, 4, 4, 0, 0);
    Paint_SetBitsPerPixel(4);
    Paint_Clear(0xf0);
    for(int i = 0; i < 8; i++) assert(buf[i] == 0xff);
    Paint_SetPixel(0, 4, 0);
    Paint_SetPixel(4, 4, 0);
    for(int i = 0; i < 8; i++) assert(buf[i] == 0xff);
    Paint_SetPixel(3, 3, 0);
    unsigned black = 0;
    for(int i = 0; i < 8; i++) { black += ((buf[i] & 15) == 0); black += ((buf[i] >> 4) == 0); }
    assert(black == 1);
    free(buf);
}

int main(void)
{
    const char *title = "1234567890123456789012345678901234567890";
    const char *status = "123456789012345678901234";
    const char *label = "ABCDEFGHIJKLMNOPQRST";
    const char *value = "1234567890123456789012345678901234567890abcdefgh";
    char key[32];
    pixel_boundaries();
    clear_content();
    setenv("PISCHED_BATTERY", "76.5", 1);
    assert(SinglePage_ValidateContent() == 0);
    assert(SinglePage_Show(PANEL_WIDTH, PANEL_HEIGHT, 0, false, 0) == 0);
    text_at(50, 68, "PI STATUS", &Font20, 12, false);
    text_at(90, 270, "DEVICE", &Font16, 15, false);
    text_at(90, 298, "PI ZERO2", &Font20, 15, false);
    text_at(1272, 676, "76.5%", &Font20, 13, false);

    setenv("PISCHED_PAGE_TITLE", title, 1);
    setenv("PISCHED_PAGE_STATUS", status, 1);
    for(unsigned i = 1; i <= 6; i++) {
        snprintf(key, sizeof(key), "PISCHED_PAGE_LABEL_%u", i);
        setenv(key, label, 1);
        snprintf(key, sizeof(key), "PISCHED_PAGE_VALUE_%u", i);
        setenv(key, value, 1);
    }
    assert(SinglePage_ValidateContent() == 0);
    assert(SinglePage_Show(PANEL_WIDTH, PANEL_HEIGHT, 0, false, 0) == 0);
    text_at(50, 68, title, &Font20, 12, false);
    text_at(1272, 1022, status, &Font20, 13, false);
    for(unsigned i = 0; i < 6; i++) {
        text_at(90, 270 + i * 155, label, &Font16, 15, false);
        text_at(90, 298 + i * 155, title, &Font20, 15, false);
        text_at(90, 324 + i * 155, "abcdefgh", &Font20, 15, false);
        assert(pixel(90 + 40 * Font20.Width, 298 + i * 155) == 15);
    }
    save_preview();
    assert(SinglePage_Show(PANEL_WIDTH, PANEL_HEIGHT, 0, true, 1) == 0);
    assert(capture_width == 1856);
    text_at(50, 68, title, &Font20, 12, true);
    text_at(90, 1099, "abcdefgh", &Font20, 15, true);
    text_at(1261, 1022, status, &Font20, 13, true);

    setenv("PISCHED_PAGE_TITLE", "12345678901234567890123456789012345678901", 1);
    rejected();
    setenv("PISCHED_PAGE_TITLE", "Invalid\nTitle", 1);
    rejected();
    setenv("PISCHED_PAGE_TITLE", "\xc3\xa9", 1);
    rejected();
    setenv("PISCHED_PAGE_TITLE", title, 1);
    setenv("PISCHED_PAGE_STATUS", "1234567890123456789012345", 1);
    rejected();
    setenv("PISCHED_PAGE_STATUS", status, 1);
    setenv("PISCHED_PAGE_LABEL_6", "123456789012345678901", 1);
    rejected();
    setenv("PISCHED_PAGE_LABEL_6", label, 1);
    setenv("PISCHED_PAGE_VALUE_6", "1234567890123456789012345678901234567890123456789", 1);
    rejected();
    setenv("PISCHED_PAGE_VALUE_6", "bad\tvalue", 1);
    rejected();
    unsetenv("PISCHED_PAGE_VALUE_6");
    rejected();
    setenv("PISCHED_PAGE_VALUE_6", "", 1);
    assert(SinglePage_ValidateContent() == 0);
    setenv("PISCHED_PAGE_LABEL_7", "SEVENTH", 1);
    rejected();
    clear_content();
    setenv("PISCHED_PAGE_LABEL_1", "Missing title", 1);
    rejected();
    clear_content();
    int previous = captures;
    assert(SinglePage_Show(320, 200, 0, false, 0) != 0);
    assert(SinglePage_Show(0, 0, 0, false, 0) != 0);
    assert(captures == previous);
    return 0;
}
