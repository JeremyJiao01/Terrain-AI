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
