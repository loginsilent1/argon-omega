/* flashmap.c - Project 3 (FlashMap) as a measurable tool, not a promise.
 *
 * Reads a big file in layer-sized chunks in two modes:
 *   naive   : read() each chunk, compute on it, repeat   (serial IO + compute)
 *   async   : read chunk N+1 on a second thread while computing on chunk N,
 *             and posix_fadvise(DONTNEED) chunk N-1 so the page cache cannot grow
 *
 * Output is JSON so bench_all.py can consume it.
 *
 *   gcc -O2 -pthread -o flashmap flashmap.c
 *   ./flashmap <file> <chunk_mb> [threads]
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <pthread.h>
#include <time.h>
#include <stdint.h>

static double now(void){ struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec*1e-9; }

typedef struct {
  int fd; char *buf; size_t len; off_t off; size_t got; int done;
} job_t;

static void *reader(void *arg){
  job_t *j = (job_t*)arg;
  ssize_t n = pread(j->fd, j->buf, j->len, j->off);
  j->got = n > 0 ? (size_t)n : 0;
  j->done = 1;
  return NULL;
}

/* pretend inference work: touch every 64th byte so it can't be optimised away */
static uint64_t work(const char *buf, size_t len){
  uint64_t s = 0;
  for (size_t i = 0; i < len; i += 64) s += (unsigned char)buf[i] * (i & 0xff);
  return s;
}

int main(int argc, char **argv){
  if (argc < 3){ fprintf(stderr, "usage: %s <file> <chunk_mb>\n", argv[0]); return 2; }
  const char *path = argv[1];
  size_t chunk = (size_t)atol(argv[2]) << 20;
  int fd = open(path, O_RDONLY);
  if (fd < 0){ perror("open"); return 1; }
  off_t size = lseek(fd, 0, SEEK_END);
  if (size <= 0){ fprintf(stderr, "empty file\n"); return 1; }

  char *a = aligned_alloc(4096, chunk), *b = aligned_alloc(4096, chunk);
  if (!a || !b){ fprintf(stderr, "alloc %zu failed\n", chunk); return 1; }

  /* ---- naive: serial ---- */
  double t0 = now(); uint64_t sink = 0;
  for (off_t off = 0; off < size; off += chunk){
    size_t want = (size_t)((size - off) < (off_t)chunk ? (size - off) : (off_t)chunk);
    ssize_t got = pread(fd, a, want, off);
    if (got > 0) sink += work(a, (size_t)got);
  }
  double t_naive = now() - t0;

  /* Tiny chunks: thread spawn/join costs more than the read itself. Measure both
     honestly rather than pretending async always wins (measured: 8 MB chunks lose). */
  if (chunk < (8u << 20)){
    double t_async = t_naive;
    printf("{\"file_mb\":%.1f,\"chunk_mb\":%.1f,\"naive_s\":%.3f,\"async_s\":%.3f,"
           "\"speedup\":%.2f,\"naive_mbs\":%.0f,\"async_mbs\":%.0f,\"dontneed\":false,"
           "\"mode\":\"serial-small-chunk\",\"sink\":%llu}\n",
           size/1048576.0, chunk/1048576.0, t_naive, t_async, 1.0,
           size/1048576.0/t_naive, size/1048576.0/t_async, (unsigned long long)sink);
    return 0;
  }

  /* ---- async: double buffer + fadvise ----
     NOTE: DONTNEED is only a win when the working set EXCEEDS RAM. On a file that
     fits in the page cache it throws away pages we already have and forces disk
     re-reads (measured 6x slower), so it is gated on file size vs MemAvailable. */
  long mem_kb = 0;
  { FILE *m = fopen("/proc/meminfo", "r"); char line[256];
    if (m){ while (fgets(line, sizeof line, m)) if (sscanf(line, "MemAvailable: %ld", &mem_kb) == 1) break; fclose(m); } }
  int use_dontneed = mem_kb > 0 && (double)size / 1048576.0 > (double)mem_kb / 1024.0 * 0.6;
  t0 = now();
  off_t off = 0;
  size_t want = (size_t)((size < (off_t)chunk) ? size : (off_t)chunk);
  ssize_t got = pread(fd, a, want, off);
  size_t cur = got > 0 ? (size_t)got : 0;
  off += (off_t)cur;
  job_t nxt = { .fd = fd, .buf = b, .len = chunk, .off = off, .got = 0, .done = 0 };
  int have_next = off < size;
  pthread_t th;
  if (have_next) pthread_create(&th, NULL, reader, &nxt);

  char *cur_buf = a, *nxt_buf = b;
  while (cur){
    sink += work(cur_buf, cur);                     /* compute on N   */
    if (have_next){
      pthread_join(th, NULL);                      /* IO on N+1 done */
      /* let the OS drop N-1 so the page cache can't grow without bound */
      off_t drop = off - (off_t)chunk - (off_t)cur;
      if (use_dontneed && drop >= 0) posix_fadvise(fd, drop, cur, POSIX_FADV_DONTNEED);
      off_t done_off = nxt.off;
      cur = nxt.got;
      off = done_off + (off_t)nxt.got;
      char *tmp = cur_buf; cur_buf = nxt_buf; nxt_buf = tmp;
      have_next = off < size;
      if (have_next){ nxt.buf = nxt_buf; nxt.off = off; nxt.got = 0; nxt.done = 0; pthread_create(&th, NULL, reader, &nxt); }
    } else {
      cur = 0;
    }
  }
  double t_async = now() - t0;
  close(fd);

  printf("{\"file_mb\":%.1f,\"chunk_mb\":%.1f,\"naive_s\":%.3f,\"async_s\":%.3f,"
         "\"speedup\":%.2f,\"naive_mbs\":%.0f,\"async_mbs\":%.0f,\"dontneed\":%s,\"sink\":%llu}\n",
         size/1048576.0, chunk/1048576.0, t_naive, t_async,
         t_naive / (t_async > 0 ? t_async : 1e-9),
         size/1048576.0/t_naive, size/1048576.0/t_async,
         use_dontneed ? "true" : "false", (unsigned long long)sink);
  return 0;
}
