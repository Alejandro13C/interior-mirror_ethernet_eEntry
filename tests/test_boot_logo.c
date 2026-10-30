/* Exercise the real JPEG renderer against framebuffer memory, without a Pi. */
#include <assert.h>
#include <stdarg.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/fb.h>
#include <string.h>
#include <stdint.h>

static unsigned char memory[800 * 480 * 4];
static unsigned bits = 32;
static int fake_open(const char *path, int flags, ...) {
    (void)flags;
    assert(!strcmp(path, "/dev/fb0"));
    return 123;
}
static int fake_close(int fd) { assert(fd == 123); return 0; }
static int fake_ioctl(int fd, unsigned long request, ...) {
    assert(fd == 123);
    va_list args;
    va_start(args, request);
    void *out = va_arg(args, void *);
    va_end(args);
    if (request == FBIOGET_FSCREENINFO) {
        struct fb_fix_screeninfo *f = out;
        memset(f, 0, sizeof(*f));
        f->type = FB_TYPE_PACKED_PIXELS; f->visual = FB_VISUAL_TRUECOLOR;
        f->line_length = 800 * bits / 8; f->smem_len = sizeof(memory);
    } else {
        assert(request == FBIOGET_VSCREENINFO);
        struct fb_var_screeninfo *v = out;
        memset(v, 0, sizeof(*v));
        v->xres = 800; v->yres = 480; v->bits_per_pixel = bits;
        v->red = (struct fb_bitfield){bits == 16 ? 11 : 16, bits == 16 ? 5 : 8, 0};
        v->green = (struct fb_bitfield){bits == 16 ? 5 : 8, bits == 16 ? 6 : 8, 0};
        v->blue = (struct fb_bitfield){0, bits == 16 ? 5 : 8, 0};
    }
    return 0;
}
static void *fake_mmap(void *addr, size_t len, int prot, int flags, int fd, off_t off) {
    (void)addr; (void)prot; (void)flags;
    assert(len == sizeof(memory) && fd == 123 && off == 0);
    return memory;
}
static int fake_munmap(void *addr, size_t len) {
    assert(addr == memory && len == sizeof(memory)); return 0;
}
#define open fake_open
#define close fake_close
#define ioctl fake_ioctl
#define mmap fake_mmap
#define munmap fake_munmap
#define main boot_logo_main
#include "../tools/boot-logo.c"
#undef main

int main(void) {
    char *images[] = {"logo/logo-renault-sport-57-x-85-cm.jpg",
                      "logo/logo-renault-sport-invertido.jpg"};
    for (unsigned image = 0; image < 2; image++) {
        char *args[] = {"boot-logo", images[image], NULL};
        setenv("CAMERA_DISPLAY_PAR", "16/15", 1);
        for (bits = 16; bits <= 32; bits += 8) {
            memset(memory, 0x55, sizeof(memory));
            setenv("CAMERA_ROTATION", "0", 1);
            assert(boot_logo_main(2, args) == 0);
            unsigned bytes = bits / 8;
            /* A square image occupies 450x480 pixels on this panel. */
            assert(memory[(200 * 800 + 174) * bytes] == 0);
            assert(memory[(200 * 800 + 625) * bytes] == 0);
            assert((memory[175 * bytes] != 0) == (image == 0));
            unsigned nonblack = 0;
            for (unsigned i = 0; i < 800 * 480 * bytes; i++) nonblack += memory[i] != 0;
            assert(nonblack > 10000);
            unsigned char original[sizeof(memory)];
            memcpy(original, memory, sizeof(memory));
            setenv("CAMERA_ROTATION", "180", 1);
            assert(boot_logo_main(2, args) == 0);
            for (unsigned y = 0; y < 480; y++) {
                for (unsigned x = 175; x < 625; x++) {
                    assert(!memcmp(memory + (y * 800 + x) * bytes,
                        original + ((479 - y) * 800 + 799 - x) * bytes, bytes));
                }
            }
        }
    }
    puts("boot-logo: original/inverted JPEG, centring, pixel aspect, 16/24/32-bit and rotation passed");
    return 0;
}
