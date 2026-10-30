/* One-shot fbcon splash. Exit before kmssink starts: no DRM master or viewer
 * remains running alongside the camera. libjpeg's fatal errors exit this helper;
 * display-console.sh then keeps the normal SIN VIDEO fallback. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/fb.h>
#include <jpeglib.h>

static double display_par(void) {
    const char *value = getenv("CAMERA_DISPLAY_PAR");
    if (!value || !*value) return 1.0;
    char *end;
    double n = strtod(value, &end), d = 1.0;
    if (*end == '/') d = strtod(end + 1, &end);
    double par = n / d;
    return !*end && isfinite(par) && par >= 0.25 && par <= 4 ? par : 1.0;
}

static int valid_field(struct fb_bitfield f, unsigned bits) {
    return !f.msb_right && f.length <= 8 && f.offset <= bits &&
           f.length <= bits - f.offset;
}

static uint32_t channel(unsigned value, struct fb_bitfield f) {
    if (!f.length) return 0;
    return ((value * ((1u << f.length) - 1u) + 127u) / 255u) << f.offset;
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "Usage: boot-logo image.jpg\n"); return 1; }
    FILE *file = fopen(argv[1], "rb");
    if (!file) { perror("boot-logo: image"); return 1; }
    struct jpeg_decompress_struct jpeg;
    struct jpeg_error_mgr errors;
    jpeg.err = jpeg_std_error(&errors);
    jpeg_create_decompress(&jpeg);
    jpeg_stdio_src(&jpeg, file);
    jpeg_read_header(&jpeg, TRUE);
    if (!jpeg.image_width || !jpeg.image_height ||
        jpeg.image_width > 4096 || jpeg.image_height > 4096) {
        fprintf(stderr, "boot-logo: image dimensions unsupported\n");
        jpeg_destroy_decompress(&jpeg); fclose(file); return 1;
    }
    jpeg.out_color_space = JCS_RGB;
    jpeg_start_decompress(&jpeg);
    unsigned width = jpeg.output_width, height = jpeg.output_height;
    size_t stride = (size_t)width * 3;
    unsigned char *rgb = malloc(stride * height);
    if (!rgb) { jpeg_destroy_decompress(&jpeg); fclose(file); return 1; }
    while (jpeg.output_scanline < height) {
        JSAMPROW row = rgb + jpeg.output_scanline * stride;
        jpeg_read_scanlines(&jpeg, &row, 1);
    }
    jpeg_finish_decompress(&jpeg);
    jpeg_destroy_decompress(&jpeg);
    fclose(file);

    int result = 1;
    int fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    if (fd < 0) { perror("boot-logo: /dev/fb0"); free(rgb); return 1; }
    struct fb_fix_screeninfo fix;
    struct fb_var_screeninfo v;
    if (ioctl(fd, FBIOGET_FSCREENINFO, &fix) || ioctl(fd, FBIOGET_VSCREENINFO, &v)) {
        perror("boot-logo: framebuffer information"); goto done;
    }
    unsigned bytes = v.bits_per_pixel / 8;
    if (fix.type != FB_TYPE_PACKED_PIXELS || fix.visual != FB_VISUAL_TRUECOLOR ||
        (v.bits_per_pixel != 16 && v.bits_per_pixel != 24 && v.bits_per_pixel != 32) ||
        !v.xres || !v.yres || !v.red.length || !v.green.length || !v.blue.length ||
        !valid_field(v.red, v.bits_per_pixel) || !valid_field(v.green, v.bits_per_pixel) ||
        !valid_field(v.blue, v.bits_per_pixel) || !valid_field(v.transp, v.bits_per_pixel) ||
        ((uint64_t)v.xoffset + v.xres) * bytes > fix.line_length ||
        ((uint64_t)v.yoffset + v.yres) * fix.line_length > fix.smem_len) {
        fprintf(stderr, "boot-logo: framebuffer layout unsupported\n"); goto done;
    }
    unsigned char *fb = mmap(NULL, fix.smem_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (fb == MAP_FAILED) { perror("boot-logo: mmap"); goto done; }
    double par = display_par();
    double scale = fmin((double)v.xres * par / width, (double)v.yres / height);
    unsigned dw = (unsigned)lround(width * scale / par);
    unsigned dh = (unsigned)lround(height * scale);
    if (!dw) dw = 1;
    if (!dh) dh = 1;
    if (dw > v.xres) dw = v.xres;
    if (dh > v.yres) dh = v.yres;
    unsigned left = (v.xres - dw) / 2, top = (v.yres - dh) / 2;
    const char *rotation = getenv("CAMERA_ROTATION");
    int rotate = rotation && !strcmp(rotation, "180");
    for (unsigned y = 0; y < v.yres; y++) {
        for (unsigned x = 0; x < v.xres; x++) {
            unsigned r = 0, g = 0, b = 0;
            if (x >= left && x - left < dw && y >= top && y - top < dh) {
                unsigned dx = x - left, dy = y - top;
                if (rotate) { dx = dw - 1 - dx; dy = dh - 1 - dy; }
                unsigned sx = (uint64_t)dx * width / dw;
                unsigned sy = (uint64_t)dy * height / dh;
                const unsigned char *p = rgb + sy * stride + sx * 3;
                r = p[0]; g = p[1]; b = p[2];
            }
            uint32_t pixel = channel(r, v.red) | channel(g, v.green) |
                             channel(b, v.blue) | channel(255, v.transp);
            /* The supported Pi targets use little-endian framebuffer pixels. */
            unsigned char *dest = fb + (size_t)(y + v.yoffset) * fix.line_length +
                                  (size_t)(x + v.xoffset) * bytes;
            for (unsigned i = 0; i < bytes; i++) dest[i] = pixel >> (8 * i);
        }
    }
    munmap(fb, fix.smem_len);
    result = 0;
done:
    close(fd);
    free(rgb);
    return result;
}
