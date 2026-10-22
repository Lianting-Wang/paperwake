#include "page.h"

#include "../lib/Config/Debug.h"
#include "../lib/GUI/GUI_Paint.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define PAGE_WHITE      0xF0
#define PAGE_BLACK      0x00
#define PAGE_MID_GRAY   0xC0
#define PAGE_LIGHT_GRAY 0xD0
#define PAGE_DARK_GRAY  0x80

#define CN_HELLO "\xC4\xE3\xBA\xC3"
#define CN_RPI   "\xCA\xF7\xDD\xAE\xC5\xC9"

#define PISUGAR_SOCKET_PATH "/tmp/pisugar-server.sock"
#define PISUGAR_REQUEST     "get battery\n"

extern UBYTE isColor;

static UWORD MinUword(UWORD a, UWORD b)
{
    return (a < b) ? a : b;
}

static UWORD MaxUword(UWORD a, UWORD b)
{
    return (a > b) ? a : b;
}

static UWORD ClampUword(UWORD value, UWORD min_value, UWORD max_value)
{
    if(value < min_value) {
        return min_value;
    }
    if(value > max_value) {
        return max_value;
    }
    return value;
}

static void FillBox(UWORD x0, UWORD y0, UWORD x1, UWORD y1, UWORD fill_color)
{
    Paint_DrawRectangle(x0, y0, x1, y1, fill_color, DOT_PIXEL_1X1, DRAW_FILL_FULL);
    Paint_DrawRectangle(x0, y0, x1, y1, PAGE_BLACK, DOT_PIXEL_1X1, DRAW_FILL_EMPTY);
}

static bool ReadBatteryValue(char *buffer, size_t buffer_size)
{
    int socket_fd;
    struct sockaddr_un server_addr;
    struct timeval timeout;
    char response[128];
    ssize_t read_size;
    char *value_start;
    char *value_end;

    if(buffer == NULL || buffer_size == 0) {
        return false;
    }

    socket_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if(socket_fd < 0) {
        return false;
    }

    timeout.tv_sec = 1;
    timeout.tv_usec = 0;
    setsockopt(socket_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(socket_fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sun_family = AF_UNIX;
    strncpy(server_addr.sun_path, PISUGAR_SOCKET_PATH, sizeof(server_addr.sun_path) - 1);

    if(connect(socket_fd, (struct sockaddr *)&server_addr, sizeof(server_addr)) != 0) {
        close(socket_fd);
        return false;
    }

    if(write(socket_fd, PISUGAR_REQUEST, sizeof(PISUGAR_REQUEST) - 1) < 0) {
        close(socket_fd);
        return false;
    }

    read_size = read(socket_fd, response, sizeof(response) - 1);
    close(socket_fd);
    if(read_size <= 0) {
        return false;
    }

    response[read_size] = '\0';
    value_start = response;
    while(*value_start != '\0' && ((*value_start < '0' || *value_start > '9') && *value_start != '.')) {
        value_start++;
    }
    if(*value_start == '\0') {
        return false;
    }

    value_end = value_start;
    while((*value_end >= '0' && *value_end <= '9') || *value_end == '.') {
        value_end++;
    }
    *value_end = '\0';

    snprintf(buffer, buffer_size, "%s%%", value_start);
    return true;
}

static void FormatCurrentTime(char *buffer, size_t buffer_size)
{
    time_t now;
    struct tm local_tm;

    if(buffer == NULL || buffer_size == 0) {
        return;
    }

    now = time(NULL);
    if(localtime_r(&now, &local_tm) == NULL) {
        snprintf(buffer, buffer_size, "N/A");
        return;
    }

    strftime(buffer, buffer_size, "%Y-%m-%d %H:%M", &local_tm);
}

static void DrawInfoCard(UWORD x0, UWORD y0, UWORD x1, UWORD y1, UWORD padding, const char *title, const char *value)
{
    UWORD divider_y;
    UWORD value_y;

    FillBox(x0, y0, x1, y1, PAGE_LIGHT_GRAY);
    Paint_DrawString_EN(x0 + padding, y0 + padding, title, &Font20, PAGE_BLACK, PAGE_LIGHT_GRAY);

    divider_y = y0 + padding + Font20.Height + 10;
    Paint_DrawLine(x0 + padding, divider_y, x1 - padding, divider_y, PAGE_DARK_GRAY, DOT_PIXEL_1X1, LINE_STYLE_SOLID);

    value_y = divider_y + padding;
    Paint_DrawString_EN(x0 + padding, value_y, value, &Font20, PAGE_BLACK, PAGE_LIGHT_GRAY);
}

static void SinglePage_SetDisplayMode(int mode)
{
    isColor = 0;

    if(mode == 3) {
        Paint_SetRotate(ROTATE_0);
        Paint_SetMirroring(MIRROR_NONE);
        isColor = 1;
    } else if(mode == 2) {
        Paint_SetRotate(ROTATE_0);
        Paint_SetMirroring(MIRROR_HORIZONTAL);
    } else if(mode == 1) {
        Paint_SetRotate(ROTATE_0);
        Paint_SetMirroring(MIRROR_HORIZONTAL);
    } else {
        Paint_SetRotate(ROTATE_0);
        Paint_SetMirroring(MIRROR_NONE);
    }
}

UBYTE SinglePage_Show(UWORD panel_width, UWORD panel_height, UDOUBLE target_memory_addr, bool four_byte_align, int display_mode)
{
    UWORD render_width;
    UDOUBLE image_size;
    UBYTE *frame_buffer;
    UWORD smallest_edge;
    UWORD margin;
    UWORD header_height;
    UWORD footer_height;
    UWORD content_top;
    UWORD content_bottom;
    UWORD content_height;
    UWORD inner_width;
    UWORD column_gap;
    UWORD left_width;
    UWORD right_width;
    UWORD left_x;
    UWORD right_x;
    UWORD left_y;
    UWORD left_bottom;
    UWORD text_x;
    UWORD text_y;
    UWORD line_gap;
    UWORD card_gap;
    UWORD card_height;
    UWORD card_y;
    UWORD footer_y;
    char text[64];
    char current_time[32];
    char battery_value[32];

    render_width = four_byte_align ? (panel_width - (panel_width % 32)) : panel_width;
    if(render_width == 0 || panel_height == 0) {
        Debug("Invalid panel geometry\r\n");
        return 1;
    }

    image_size = ((render_width * 4 % 8 == 0) ? (render_width * 4 / 8) : (render_width * 4 / 8 + 1)) * panel_height;
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
    if(!ReadBatteryValue(battery_value, sizeof(battery_value))) {
        snprintf(battery_value, sizeof(battery_value), "N/A");
    }

    smallest_edge = MinUword(render_width, panel_height);
    margin = ClampUword(smallest_edge / 28, 18, 56);
    header_height = ClampUword(panel_height / 7, 80, 180);
    footer_height = ClampUword(panel_height / 11, 48, 110);
    content_top = header_height + margin;
    content_bottom = panel_height - footer_height - margin;
    if(content_bottom <= content_top + 1) {
        free(frame_buffer);
        return 1;
    }

    content_height = content_bottom - content_top;
    inner_width = render_width - (margin * 2);
    column_gap = margin;
    right_width = inner_width / 3;
    left_width = inner_width - right_width - column_gap;
    left_x = margin;
    right_x = left_x + left_width + column_gap;
    left_y = content_top;
    left_bottom = content_bottom - 1;

    FillBox(0, 0, render_width - 1, header_height - 1, PAGE_MID_GRAY);
    Paint_DrawString_CN(margin, margin + 2, CN_RPI, &Font24CN, PAGE_BLACK, PAGE_MID_GRAY);
    Paint_DrawString_EN(margin + Font24CN.Width * 3 + margin / 2, margin + 4, "PI STATUS", &Font20, PAGE_BLACK, PAGE_MID_GRAY);
    Paint_DrawString_CN(render_width - margin - (Font24CN.Width * 2), margin + 2, CN_HELLO, &Font24CN, PAGE_BLACK, PAGE_MID_GRAY);
    Paint_DrawLine(0, header_height, render_width - 1, header_height, PAGE_BLACK, DOT_PIXEL_1X1, LINE_STYLE_SOLID);

    FillBox(left_x, left_y, left_x + left_width - 1, left_bottom, PAGE_WHITE);

    text_x = left_x + margin;
    text_y = left_y + margin;
    line_gap = MaxUword(Font16.Height + margin / 2, 26);

    Paint_DrawString_EN(text_x, text_y, "SYSTEM", &Font20, PAGE_BLACK, PAGE_WHITE);
    text_y += Font20.Height + margin;

    Paint_DrawString_EN(text_x, text_y, "DEVICE: PI ZERO2", &Font16, PAGE_BLACK, PAGE_WHITE);
    text_y += line_gap;

    snprintf(text, sizeof(text), "PANEL: %ux%u", (unsigned int)panel_width, (unsigned int)panel_height);
    Paint_DrawString_EN(text_x, text_y, text, &Font16, PAGE_BLACK, PAGE_WHITE);
    text_y += line_gap;

    snprintf(text, sizeof(text), "MODE: %d", display_mode);
    Paint_DrawString_EN(text_x, text_y, text, &Font16, PAGE_BLACK, PAGE_WHITE);
    text_y += line_gap;

    Paint_DrawString_EN(text_x, text_y, "VCOM: -1.56V", &Font16, PAGE_BLACK, PAGE_WHITE);
    text_y += line_gap;

    Paint_DrawString_EN(text_x, text_y, "STATUS: READY", &Font16, PAGE_BLACK, PAGE_WHITE);
    text_y += line_gap + margin / 2;

    Paint_DrawString_CN(text_x, text_y, CN_HELLO, &Font24CN, PAGE_BLACK, PAGE_WHITE);
    Paint_DrawString_CN(text_x + Font24CN.Width * 2 + margin / 2, text_y, CN_RPI, &Font24CN, PAGE_BLACK, PAGE_WHITE);

    card_gap = MaxUword(margin / 2, 14);
    card_height = (content_height - (card_gap * 2)) / 3;
    card_y = left_y;

    DrawInfoCard(right_x, card_y, right_x + right_width - 1, card_y + card_height - 1, margin, "TIME", current_time);

    card_y += card_height + card_gap;
    DrawInfoCard(right_x, card_y, right_x + right_width - 1, card_y + card_height - 1, margin, "BATTERY", battery_value);

    card_y += card_height + card_gap;
    DrawInfoCard(right_x, card_y, right_x + right_width - 1, card_y + card_height - 1, margin, "STATUS", "READY");

    footer_y = panel_height - footer_height;
    Paint_DrawLine(margin, footer_y, render_width - margin, footer_y, PAGE_BLACK, DOT_PIXEL_1X1, LINE_STYLE_SOLID);
    Paint_DrawString_EN(margin, footer_y + margin / 2, "PI STATUS", &Font16, PAGE_BLACK, PAGE_WHITE);
    Paint_DrawString_CN(render_width - margin - (Font24CN.Width * 4), footer_y + margin / 4, "\xCE\xA2\xD1\xA9\xB5\xE7\xD7\xD3", &Font24CN, PAGE_BLACK, PAGE_WHITE);

    EPD_IT8951_4bp_Refresh(frame_buffer, 0, 0, render_width, panel_height, false, target_memory_addr, false);

    free(frame_buffer);
    return 0;
}
