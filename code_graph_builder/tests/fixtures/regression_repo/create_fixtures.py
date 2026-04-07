#!/usr/bin/env python3
"""Generate encoding-specific C test fixtures for regression testing.

Creates:
  string_utils.c / .h  — GBK encoded (Chinese comments & string literals)
  config_parser.c       — UTF-8 content but CRLF line endings
  data_io.c             — UTF-8 with embedded binary bytes (mixed encoding)

Run this script once before running the regression E2E tests.
"""
from pathlib import Path

HERE = Path(__file__).parent


# ── GBK encoded: string_utils.h ──────────────────────────────────────────

STRING_UTILS_H = """\
#ifndef STRING_UTILS_H
#define STRING_UTILS_H

/* 字符串工具函数 - 公共接口 */

/* 拼接两个字符串，返回新分配的内存 */
char* str_concat(const char* a, const char* b);

/* 计算字符串长度（不含末尾空字符） */
int str_length(const char* s);

/* 查找子串首次出现的位置，未找到返回 -1 */
int str_find(const char* haystack, const char* needle);

#endif /* STRING_UTILS_H */
"""

# ── GBK encoded: string_utils.c ──────────────────────────────────────────

STRING_UTILS_C = """\
#include <stdlib.h>
#include <string.h>
#include "string_utils.h"

/**
 * 拼接两个字符串。
 * 调用者负责释放返回的指针。
 */
char* str_concat(const char* a, const char* b) {
    /* 计算所需缓冲区大小 */
    int len_a = strlen(a);
    int len_b = strlen(b);
    char* result = (char*)malloc(len_a + len_b + 1);
    if (result == NULL) return NULL;  /* 内存分配失败 */
    strcpy(result, a);
    strcat(result, b);
    return result;
}

/**
 * 计算字符串长度。
 * 等价于 strlen，但接口统一。
 */
int str_length(const char* s) {
    int count = 0;
    while (s[count] != '\\0') {
        count++;
    }
    return count;
}

/**
 * 查找子串在主串中首次出现的位置。
 * 返回从 0 开始的索引，未找到返回 -1。
 */
int str_find(const char* haystack, const char* needle) {
    const char* p = strstr(haystack, needle);
    if (p == NULL) return -1;
    return (int)(p - haystack);
}
"""

# ── CRLF encoded: config_parser.c ────────────────────────────────────────

CONFIG_PARSER_C = """\
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/**
 * Parse an integer value from a config key.
 * Returns fallback if key not found.
 */
int parse_config_int(const char* key, int fallback) {
    /* Stub: in real code this would read a config file */
    if (strcmp(key, "port") == 0) return 8080;
    if (strcmp(key, "timeout") == 0) return 30;
    return fallback;
}

/**
 * Parse a string value from a config key.
 * Returns NULL if key not found.
 */
const char* parse_config_str(const char* key) {
    if (strcmp(key, "host") == 0) return "localhost";
    if (strcmp(key, "log_level") == 0) return "info";
    return NULL;
}

/**
 * Check if a config key exists.
 */
int config_has_key(const char* key) {
    return (parse_config_str(key) != NULL) ? 1 : 0;
}
"""

# ── Mixed encoding: data_io.c ────────────────────────────────────────────
# Mostly valid UTF-8, but with a binary magic-number constant that contains
# bytes 0x80-0xFF outside any valid UTF-8 sequence.

DATA_IO_C_BEFORE_BINARY = b"""\
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Binary magic number for our custom file format */
static const unsigned char MAGIC[] = {"""

DATA_IO_C_BINARY = b"0xDE, 0xAD, 0xBE, 0xEF"

DATA_IO_C_AFTER_BINARY = b"""\
};

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
        buf[size] = '\\0';
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
"""

# For truly mixed encoding, embed raw non-UTF-8 bytes in a comment
MIXED_BINARY_COMMENT = (
    b"\n/* Raw binary blob for testing: "
    + bytes(range(0x80, 0x90))  # 16 bytes that are invalid standalone UTF-8
    + b" */\n"
)


def main():
    # GBK files
    (HERE / "string_utils.h").write_bytes(STRING_UTILS_H.encode("gbk"))
    (HERE / "string_utils.c").write_bytes(STRING_UTILS_C.encode("gbk"))
    print("Created string_utils.h/.c (GBK)")

    # CRLF file — replace \n with \r\n
    crlf_content = CONFIG_PARSER_C.replace("\n", "\r\n").encode("utf-8")
    (HERE / "config_parser.c").write_bytes(crlf_content)
    print("Created config_parser.c (CRLF)")

    # Mixed encoding file — valid UTF-8 + raw binary bytes
    mixed = (
        DATA_IO_C_BEFORE_BINARY
        + DATA_IO_C_BINARY
        + DATA_IO_C_AFTER_BINARY
        + MIXED_BINARY_COMMENT
    )
    (HERE / "data_io.c").write_bytes(mixed)
    print("Created data_io.c (mixed encoding)")

    print("Done. All fixture files generated.")


if __name__ == "__main__":
    main()
