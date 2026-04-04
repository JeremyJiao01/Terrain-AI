#include <stdio.h>

/* 这是一个GBK编码的C文件 */

// 计算两个数的和
int add(int a, int b) {
    return a + b;
}

/* 打印欢迎消息 */
void print_welcome(const char* name) {
    printf("欢迎 %s\n", name);
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
