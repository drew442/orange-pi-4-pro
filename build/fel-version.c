/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Version-only frontend for the pinned sunxi-tools FEL transport. */
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include "fel_lib.h"

static int decimal(const char *text, int maximum)
{
    unsigned int value = 0;
    if (*text < '1' || *text > '9')
        return -1;
    for (; *text; ++text) {
        if (*text < '0' || *text > '9')
            return -1;
        value = value * 10 + (unsigned int)(*text - '0');
        if (value > (unsigned int)maximum)
            return -1;
    }
    return (int)value;
}

int main(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "--help") == 0) {
        puts("Usage: fel-version BUS DEVICE\n"
             "Version-only A733 FEL probe; explicit canonical decimal USB address required.\n"
             "BUS: 1..255; DEVICE: 1..127. Opens only USB VID:PID 1f3a:efe8.");
        return 0;
    }
    int bus, device;
    if (argc != 3 || (bus = decimal(argv[1], 255)) < 0 ||
        (device = decimal(argv[2], 127)) < 0) {
        fputs("Expected BUS DEVICE (canonical decimal 1..255 and 1..127); see --help.\n", stderr);
        return 2;
    }
    /* Bound transport stalls, including an upstream zero-length read loop. */
    alarm(30);
    feldev_handle *dev = feldev_open(bus, device, 0x1f3a, 0xefe8);
    if (!dev) {
        fputs("FEL open failed.\n", stderr);
        return 1;
    }
    const struct aw_fel_version *version = &dev->soc_version;
    int result = 1;
    if (memcmp(version->signature, "AWUSBFEX", 8) != 0) {
        fputs("Unexpected FEL version signature.\n", stderr);
    } else if (version->soc_id != 0x1903) {
        fprintf(stderr, "Refusing unexpected FEL SoC ID 0x%04x (expected A733 0x1903).\n",
                (unsigned int)version->soc_id);
    } else {
        printf("bus=%d device=%d signature=AWUSBFEX soc_id=0x%04x "
               "unknown_0a=0x%08x protocol=0x%04x unknown_12=0x%02x "
               "unknown_13=0x%02x scratchpad=0x%08x pad0=0x%08x pad1=0x%08x\n",
               bus, device, (unsigned int)version->soc_id,
               (unsigned int)version->unknown_0a, (unsigned int)version->protocol,
               (unsigned int)version->unknown_12, (unsigned int)version->unknown_13,
               (unsigned int)version->scratchpad, (unsigned int)version->pad[0],
               (unsigned int)version->pad[1]);
        result = 0;
    }
    feldev_done(dev);
    alarm(0);
    return result;
}
