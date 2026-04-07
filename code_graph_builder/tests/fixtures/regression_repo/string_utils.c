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
    while (s[count] != '\0') {
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
