#!/usr/bin/env python3
"""Generate GBK and GB2312 encoded C test fixture files."""
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent

C_SOURCE_GBK = """\
#include <stdio.h>

/* 这是一个GBK编码的C文件 */

// 计算两个数的和
int add(int a, int b) {
    return a + b;
}

/* 打印欢迎消息 */
void print_welcome(const char* name) {
    printf("欢迎 %s\\n", name);
}

// 结构体：用户信息
struct UserInfo {
    char name[64];
    int age;
};

// 获取用户年龄
int get_age(struct UserInfo* user) {
    return user->age;
}
"""

C_SOURCE_GB2312 = """\
#include <stdlib.h>

/* GB2312编码测试文件 */

// 分配内存缓冲区
void* alloc_buffer(int size) {
    return malloc(size);
}

// 释放内存缓冲区
void free_buffer(void* ptr) {
    free(ptr);
}
"""

def main():
    gbk_path = FIXTURES_DIR / "test_gbk.c"
    gbk_path.write_bytes(C_SOURCE_GBK.encode("gbk"))
    print(f"Created {gbk_path}")

    gb2312_path = FIXTURES_DIR / "test_gb2312.c"
    gb2312_path.write_bytes(C_SOURCE_GB2312.encode("gb2312"))
    print(f"Created {gb2312_path}")

    utf8_path = FIXTURES_DIR / "test_utf8.c"
    utf8_path.write_text(C_SOURCE_GBK, encoding="utf-8")
    print(f"Created {utf8_path}")

if __name__ == "__main__":
    main()
