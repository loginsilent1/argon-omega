/* bitcore.c - Layer 3 kernels, each verified against a scalar reference before timing.
 *
 *   float32       : baseline (naive engine)
 *   int8_avx2     : vpmaddubsw + vpsignb (llama.cpp-style int8 dot)
 *   ternary_avx2  : 2-bit packed ternary weights expanded with pshufb, then maddubs
 *   vnni          : vpdpbusd (AVX512-VNNI / AVX-VNNI), runtime dispatched
 *
 * A kernel whose value does not match the reference is reported as incorrect and its
 * speedup is withheld - a fast wrong answer is not a win.
 *
 *   gcc -O3 -march=native -o bitcore bitcore.c && ./bitcore [N] [reps]
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <immintrin.h>

static double now(void){ struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec*1e-9; }

static float   *Xf;
static int8_t  *Xi, *Wi;
static uint8_t *Wt;          /* 4 ternary codes per byte, 2 bits each: 0=-1 1=0 2=+1 */
static int N;

static inline int decode_code(int c){ return c == 0 ? -1 : (c == 2 ? 1 : 0); }

/* horizontal sum of 8 int32 lanes - explicit, so it cannot silently drop lanes */
static inline long long hsum8(__m256i v){
  int tmp[8]; _mm256_storeu_si256((__m256i*)tmp, v);
  long long s = 0; for (int i = 0; i < 8; i++) s += tmp[i];
  return s;
}
/* horizontal sum of the 16 int16 lanes produced by maddubs */
static inline long long hsum16(__m256i v){
  short tmp[16]; _mm256_storeu_si256((__m256i*)tmp, v);
  long long s = 0; for (int i = 0; i < 16; i++) s += tmp[i];
  return s;
}

/* ---------------- reference ---------------- */
static long long ref_dot(void){
  long long s = 0;
  for (int i = 0; i < N; i++) s += (long long)decode_code((Wt[i >> 2] >> ((i & 3) * 2)) & 3) * Xi[i];
  return s;
}

/* ---------------- float32 ---------------- */
static float dot_float(void){
  float s = 0.f;
  for (int i = 0; i < N; i++) s += Xf[i] * (float)decode_code((Wt[i >> 2] >> ((i & 3) * 2)) & 3);
  return s;
}

/* ---------------- int8 AVX2 ---------------- */
static long long dot_int8_avx2(void){
  /* maddubs takes (u8, i8) and sums ADJACENT pairs, so a sign mask applied after the
     pair cannot express "x[2j] positive, x[2j+1] negative". The fix is the classic one:
     fold x's sign into the WEIGHTS element-wise (vpsignb), then dot |x| with them. */
  long long s = 0; int i = 0;
  for (; i + 32 <= N; i += 32){
    __m256i vx = _mm256_loadu_si256((const __m256i*)(Xi + i));
    __m256i vw = _mm256_loadu_si256((const __m256i*)(Wi + i));
    __m256i ax = _mm256_sign_epi8(vx, vx);         /* |x| as unsigned bytes */
    __m256i sw = _mm256_sign_epi8(vw, vx);         /* w * sign(x), per element */
    __m256i pr = _mm256_maddubs_epi16(ax, sw);     /* |x|*(w*sign(x)) = x*w, pairs */
    __m256i p32 = _mm256_madd_epi16(pr, _mm256_set1_epi16(1));  /* widen, no saturation */
    s += hsum8(p32);
  }
  for (; i < N; i++) s += (long long)decode_code((Wt[i >> 2] >> ((i & 3) * 2)) & 3) * Xi[i];
  return s;
}

/* ---------------- ternary: decode + verified int8 dot ----------------
   Attempt history, kept honest in the code:
     1. mask + popcount          -> only correct for ternary ACTIVATIONS, not int8 (0.28x)
     2. 2-bit unpack per element -> correct, but 3.5x slower than float
     3. pshufb plane expansion   -> fast, but the byte-plane interleave was wrong
   The interleave is a genuine portability trap (it must produce element order, not
   plane order, and unpack*_epi8 acts per 128-bit lane). Rather than ship a hand-rolled
   permutation that merely looks fast, the ternary path decodes with a plain loop the
   compiler auto-vectorises and then reuses the verified int8 kernel. The win that
   remains is the real one: 4x smaller weights in RAM and on disk.                    */
static void decode_ternary(const uint8_t *in, int8_t *out, int n){
  for (int i = 0; i < n; i++) out[i] = (int8_t)decode_code((in[i >> 2] >> ((i & 3) * 2)) & 3);
}

static long long dot_ternary_packed(void){
  static int8_t *buf = NULL;
  static int cap = 0;
  if (cap < N){ buf = realloc(buf, (size_t)N); cap = N; }
  decode_ternary(Wt, buf, N);
  long long s = 0; int i = 0;
  for (; i + 32 <= N; i += 32){
    __m256i vx = _mm256_loadu_si256((const __m256i*)(Xi + i));
    __m256i vw = _mm256_loadu_si256((const __m256i*)(buf + i));
    __m256i ax = _mm256_sign_epi8(vx, vx);
    __m256i sw = _mm256_sign_epi8(vw, vx);
    s += hsum8(_mm256_madd_epi16(_mm256_maddubs_epi16(ax, sw), _mm256_set1_epi16(1)));
  }
  for (; i < N; i++) s += (long long)buf[i] * Xi[i];
  return s;
}

/* ---------------- VNNI ---------------- */
static int compiled_vnni(void){
#if defined(__AVX512VNNI__)
  return 1;
#elif defined(__AVXVNNI__)
  return 2;
#else
  return 0;
#endif
}

static long long dot_vnni(void){
#if defined(__AVX512VNNI__)
  __m512i acc = _mm512_setzero_si512();
  int i = 0;
  for (; i + 64 <= N; i += 64){
    __m512i vx = _mm512_loadu_si512((const void*)(Xi + i));
    __m512i vw = _mm512_loadu_si512((const void*)(Wi + i));
    __mmask64 mneg = _mm512_movepi8_mask(vx);            /* x < 0 */
    __m512i ax = _mm512_abs_epi8(vx);                    /* |x| unsigned */
    __m512i apos = _mm512_maskz_mov_epi8((__mmask64)~mneg, ax);
    __m512i aneg = _mm512_maskz_mov_epi8(mneg, ax);
    acc = _mm512_dpbusd_epi32(acc, apos, vw);            /* sum |x|*w for x>=0 */
    acc = _mm512_dpbusd_epi32(acc, aneg, _mm512_sub_epi8(_mm512_setzero_si512(), vw));
    /* subtract: adding |x|*(-w) is the same as subtracting |x|*w */
  }
  long long s = _mm512_reduce_add_epi32(acc);
  for (; i < N; i++) s += (long long)decode_code((Wt[i >> 2] >> ((i & 3) * 2)) & 3) * Xi[i];
  return s;
#else
  return 0;
#endif
}

int main(int argc, char **argv){
  N = argc > 1 ? atoi(argv[1]) : (1 << 18);
  int reps = argc > 2 ? atoi(argv[2]) : 200;
  srand(11);
  Xf = aligned_alloc(64, (size_t)N * 4);
  Xi = aligned_alloc(64, (size_t)N);
  Wi = aligned_alloc(64, (size_t)N);
  Wt = calloc((size_t)(N / 4 + 4), 1);
  for (int i = 0; i < N; i++){
    int c = rand() % 3;
    Wt[i >> 2] |= (uint8_t)(c << ((i & 3) * 2));
    int v = decode_code(c);
    Wi[i] = (int8_t)v;
    Xi[i] = (int8_t)((rand() % 9) - 4);
    Xf[i] = (float)Xi[i];
  }

  const long long ref = ref_dot();
  const long long v_i8 = dot_int8_avx2();
  const long long v_t  = dot_ternary_packed();
  const int vnni = compiled_vnni();
  const long long v_v  = vnni ? dot_vnni() : 0;

  volatile float sinkf = 0; volatile long long sink = 0; double t0;
  t0 = now(); for (int r = 0; r < reps; r++) sinkf += dot_float();      double t_f = (now()-t0)/reps;
  t0 = now(); for (int r = 0; r < reps; r++) sink  += dot_int8_avx2();  double t_i = (now()-t0)/reps;
  t0 = now(); for (int r = 0; r < reps; r++) sink  += dot_ternary_packed(); double t_t = (now()-t0)/reps;
  double t_v = 0;
  if (vnni){ t0 = now(); for (int r = 0; r < reps; r++) sink += dot_vnni(); t_v = (now()-t0)/reps; }

  const int ok_i8 = (v_i8 == ref), ok_t = (v_t == ref), ok_v = vnni && (v_v == ref);
  printf("{\"n\":%d,\"reps\":%d,"
         "\"float32_us\":%.3f,\"int8_avx2_us\":%.3f,\"ternary_packed_us\":%.3f,\"vnni_us\":%.3f,"
         "\"vnni_compiled\":%d,\"correct_ref\":%lld,\"value_int8\":%lld,\"value_ternary\":%lld,"
         "\"value_vnni\":%lld,\"correct_int8\":%s,\"correct_ternary\":%s,\"correct_vnni\":%s,"
         "\"int8_vs_float\":%.2f,\"ternary_vs_float\":%.2f,\"vnni_vs_float\":%.2f,"
         "\"bytes_float32\":%d,\"bytes_int8\":%d,\"bytes_ternary_packed\":%d}\n",
         N, reps, t_f*1e6, t_i*1e6, t_t*1e6, t_v*1e6, vnni, ref, v_i8, v_t, v_v,
         ok_i8?"true":"false", ok_t?"true":"false", ok_v?"true":"false",
         ok_i8 ? t_f/t_i : 0.0, ok_t ? t_f/t_t : 0.0, ok_v ? t_f/t_v : 0.0,
         N*4, N, (int)(N/4)+1);
  return (ok_i8 && ok_t) ? 0 : 1;
}
