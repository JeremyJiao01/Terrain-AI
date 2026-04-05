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
