#ifndef MATH_OPS_H
#define MATH_OPS_H

/**
 * Add two integers and return the result.
 */
int add(int a, int b);

/**
 * Subtract b from a.
 */
int subtract(int a, int b);

/**
 * Compute the average of three doubles.
 */
double average(double a, double b, double c);

/**
 * Compute factorial of n (recursive).
 */
int factorial(int n);

/**
 * Clamp value between min_val and max_val.
 */
int clamp(int value, int min_val, int max_val);

#endif /* MATH_OPS_H */
