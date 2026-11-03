#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
uint8_t M[65536];
uint32_t logn;
uint16_t loga[1 << 20];
uint8_t logv[1 << 20];
int deopted;
uint8_t io_read(uint16_t a) { return M[a]; }
void io_write(uint16_t a, uint8_t v) {
  M[a] = v;
  loga[logn] = a;
  logv[logn++] = v;
}
void deopt(uint16_t pc) { deopted = pc ? pc : 1; }
uint8_t codechk(uint16_t a, uint8_t v) {
  (void)a;
  (void)v;
  return 0;
}
