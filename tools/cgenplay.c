#include <setjmp.h>
#include <stdint.h>
uint8_t IO[65536];
uint32_t logn;
uint16_t loga[1 << 20];
uint8_t logv[1 << 20];
uint8_t logw[1 << 20];
uint16_t sensitive[64];
int nsensitive;
static jmp_buf env;
static int trapped;
static void put(uint16_t a, uint8_t v, uint8_t w) {
  if (logn < (1u << 20)) {
    loga[logn] = a;
    logv[logn] = v;
    logw[logn++] = w;
  }
}
uint8_t io_read(uint16_t a) {
  for (int i = 0; i < nsensitive; i++)
    if (sensitive[i] == a)
      put(a, IO[a], 0);
  return IO[a];
}
void io_write(uint16_t a, uint8_t v) {
  IO[a] = v;
  put(a, v, 1);
}
void trap(uint16_t k) {
  trapped = k;
  longjmp(env, 1);
}
int call(void (*f)(uint8_t), uint8_t a) {
  trapped = -1;
  if (!setjmp(env))
    f(a);
  return trapped;
}
