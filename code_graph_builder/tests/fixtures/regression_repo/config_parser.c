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
