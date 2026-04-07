#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Binary magic number for our custom file format */
static const unsigned char MAGIC[] = {0xDE, 0xAD, 0xBE, 0xEF};

/**
 * Read entire file into a malloc'd buffer.
 * Caller must free the returned pointer.
 */
char* read_file(const char* path) {
    FILE* fp = fopen(path, "rb");
    if (!fp) return NULL;
    fseek(fp, 0, SEEK_END);
    long size = ftell(fp);
    rewind(fp);
    char* buf = (char*)malloc(size + 1);
    if (buf) {
        fread(buf, 1, size, fp);
        buf[size] = '\0';
    }
    fclose(fp);
    return buf;
}

/**
 * Write data to a file, overwriting if it exists.
 * Returns 0 on success, -1 on failure.
 */
int write_file(const char* path, const char* data) {
    if (!data) return -1;
    FILE* fp = fopen(path, "wb");
    if (!fp) return -1;
    size_t len = strlen(data);
    size_t written = fwrite(data, 1, len, fp);
    fclose(fp);
    return (written == len) ? 0 : -1;
}

/**
 * Check if a file starts with our magic number.
 */
int check_magic(const char* path) {
    FILE* fp = fopen(path, "rb");
    if (!fp) return 0;
    unsigned char header[4];
    if (fread(header, 1, 4, fp) != 4) {
        fclose(fp);
        return 0;
    }
    fclose(fp);
    return (header[0] == 0xDE && header[1] == 0xAD
         && header[2] == 0xBE && header[3] == 0xEF);
}

/* Raw binary blob for testing: ÄÅÇÉÑÖÜáàâäãåçéè */
