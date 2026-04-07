/**
 * Main entry point — calls functions from all other modules.
 * Purpose: generate cross-file CALLS relationships to exercise
 * flush_nodes / flush_relationships batch writes.
 */
#include <stdio.h>
#include "math_ops.h"

/* Forward declarations for functions in other translation units */
extern char* str_concat(const char* a, const char* b);
extern int   str_length(const char* s);
extern int   parse_config_int(const char* key, int fallback);
extern char* read_file(const char* path);
extern int   write_file(const char* path, const char* data);

int main(int argc, char** argv) {
    /* Cross-file calls to math_ops */
    int sum = add(10, 20);
    int diff = subtract(sum, 5);
    double avg = average(10, 20, 30);
    int fact = factorial(5);
    int clamped = clamp(150, 0, 100);

    /* Cross-file calls to string_utils (GBK encoded) */
    char* greeting = str_concat("Hello, ", "World");
    int len = str_length(greeting);

    /* Cross-file calls to config_parser (CRLF encoded) */
    int port = parse_config_int("port", 8080);

    /* Cross-file calls to data_io (mixed encoding) */
    char* content = read_file("input.txt");
    write_file("output.txt", content);

    printf("sum=%d diff=%d avg=%.1f fact=%d clamp=%d len=%d port=%d\n",
           sum, diff, avg, fact, clamped, len, port);
    return 0;
}
