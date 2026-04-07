/**
 * Math operations module.
 *
 * Provides basic arithmetic utilities used across the project.
 * Each function is documented to test API doc source-snippet embedding.
 */
#include "math_ops.h"

/**
 * Add two integers and return the result.
 *
 * @param a  First operand
 * @param b  Second operand
 * @return   Sum of a and b
 */
int add(int a, int b) {
    return a + b;
}

/**
 * Subtract b from a.
 *
 * @param a  Minuend
 * @param b  Subtrahend
 * @return   Difference a - b
 */
int subtract(int a, int b) {
    return a - b;
}

/**
 * Compute the average of three doubles.
 */
double average(double a, double b, double c) {
    return (a + b + c) / 3.0;
}

/**
 * Compute factorial of n (recursive).
 * Returns 1 for n <= 1.
 */
int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}

/**
 * Clamp value between min_val and max_val.
 */
int clamp(int value, int min_val, int max_val) {
    if (value < min_val) return min_val;
    if (value > max_val) return max_val;
    return value;
}
