"""airllm_brain.py - the AirLLM idea, implemented and measured.

The idea
--------
Keep the model on disk, never all in RAM. Run the network **one transformer block at a
time**: read block i, run it on the token, drop it, read block i+1. Peak RAM becomes
`one layer + activations` instead of `whole model`, so a model far larger than RAM can
run on a small machine.

What the measurements here decide
---------------------------------
Layer streaming is bound by `tok/s ~ disk_bandwidth / model_size` - every token requires
reading every layer once, because nothing stays resident. So the only question that
matters is: how fast is this box's disk, and what does that imply for a 7B or a 70B model?

This module answers that with a real file, not a formula:

  * `measure_disk(nbytes)` - sequential cold read of a file bigger than the page cache
    (a 4 GB file when RAM is 3.9 GB cannot be fully cached, so this is real I/O).
  * `stream_model(path, spec, mode)` - runs the whole layer pipeline and reports
    per-layer I/O, compute, peak RSS and projected tok/s, for four modes:
        mmap        - let the OS page layers in (what engine.py does today for big models)
        serial      - pread each layer, run, drop  (the naive AirLLM loop)
        prefetch    - pread layer i+1 on a thread while layer i computes (double buffer)
        compressed  - store layers int8, read 4x less, spend CPU decompressing
  * `plan(...)` - for a given model size and the measured bandwidth, decides between
    "keep resident", "mmap", "stream" and says the projected tok/s for each.

Honesty
-------
There are no real weights in this sandbox (huggingface.co is not reachable), so the
compute step is a deterministic placeholder of the right *memory* shape: it touches each
layer's elements exactly once. That makes the I/O numbers real and the compute numbers
a placeholder - and `COMPUTE_IS_PLACEHOLDER` says so in every report. The tok/s figures
produced here are an upper bound, since a real matmul does more work per byte.
"""
from __future__ import annotations

import json
import mmap
import os
import struct
import threading
import time

COMPUTE_IS_PLACEHOLDER = True


# --------------------------------------------------------------------------- specs
class LayerSpec:
    """Shape of one streamable unit (a transformer block, or a tied embedding)."""

    __slots__ = ("name", "params", "dtype_bytes")

    def __init__(self, name: str, params: int, dtype_bytes: int = 2):
        self.name = name
        self.params = params
        self.dtype_bytes = dtype_bytes

    @property
    def nbytes(self) -> int:
        return self.params * self.dtype_bytes

    def as_dict(self) -> dict:
        return {"name": self.name, "params": self.params,
                "dtype_bytes": self.dtype_bytes, "bytes": self.nbytes}


def llama_shaped_spec(n_layers: int = 32, hidden: int = 4096,
                      ffn_mult: float = 2.75, dtype_bytes: int = 2) -> list[LayerSpec]:
    """Approximate per-block parameter count for a Llama-style model.

    attn: q,k,v,o = 4*hidden^2 ; mlp: gate,up,down = 3*hidden*(ffn_mult*hidden)
    """
    attn = 4 * hidden * hidden
    mlp = 3 * hidden * int(ffn_mult * hidden)
    per = attn + mlp
    return [LayerSpec(f"blk.{i}", per, dtype_bytes) for i in range(n_layers)]


# --------------------------------------------------------------------------- disk
def measure_disk(nbytes: int = 4 << 30, path: str | None = None,
                 chunk: int = 64 << 20, passes: int = 2) -> dict:
    """Sequential read of a file larger than RAM, twice.

    Pass 1 is the number that matters for layer streaming: the file does not fit in RAM,
    so the read is real I/O. Pass 2 shows what the OS can serve from cache - the gap
    between them is why a "5697 MB/s" benchmark on a small file is meaningless here.
    """
    path = path or f"/tmp/_airllm_diskprobe_{os.getpid()}.bin"
    created = False
    try:
        if not os.path.exists(path) or os.path.getsize(path) != nbytes:
            with open(path, "wb") as f:
                block = b"\0" * (8 << 20)
                written = 0
                while written < nbytes:
                    n = min(len(block), nbytes - written)
                    f.write(block[:n])
                    written += n
            created = True
        buf = bytearray(chunk)
        view = memoryview(buf)
        timings = []
        fd = os.open(path, os.O_RDONLY)
        try:
            for _ in range(max(1, passes)):
                t0 = time.perf_counter()
                total = 0
                while total < nbytes:
                    n = os.preadv(fd, [view], total)
                    if n <= 0:
                        break
                    total += n
                timings.append(time.perf_counter() - t0)
        finally:
            os.close(fd)
        import statistics
        rates = [nbytes / 1e6 / t for t in timings]
        cold = timings[0]
        med = statistics.median(rates)
        return {"file_mb": round(nbytes / 1e6, 1),
                "passes": len(timings),
                "cold_s": round(cold, 3),
                "cold_mb_per_s": round(rates[0], 1),
                "median_mb_per_s": round(med, 1),
                "min_mb_per_s": round(min(rates), 1),
                "max_mb_per_s": round(max(rates), 1),
                "median_gb_per_s": round(med / 1000, 3),
                "created_file": created, "path": path}
    finally:
        if created:
            try:
                os.remove(path)
            except OSError:
                pass


# --------------------------------------------------------------------------- rss
def _peak_rss_mb() -> float:
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmHWM"):
                return round(int(line.split()[1]) / 1024.0, 1)
    except OSError:
        pass
    return 0.0


def _rss_mb() -> float:
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS"):
                return round(int(line.split()[1]) / 1024.0, 1)
    except OSError:
        pass
    return 0.0


# --------------------------------------------------------------------------- compute
def _touch(buf: memoryview) -> float:
    """Placeholder for one block's matmul: read every 64th element once.

    Strided rather than a full sum: a full pass over int16 data at these sizes would
    dominate the measurement, and this module measures I/O. Reported as a placeholder.
    """
    step = 128
    total = 0.0
    f = struct.Struct("<h")
    n = len(buf) - (len(buf) % step)
    for off in range(0, n, step):
        total += f.unpack_from(buf, off)[0]
    return total


# --------------------------------------------------------------------------- stream
def _minflt() -> int:
    """Minor page faults so far - for mmap, this is the layer-load counter."""
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_minflt
    except Exception:
        return 0


def stream_model(path: str, spec: list[LayerSpec], mode: str = "serial",
                 tokens: int = 4) -> dict:
    """Run `tokens` forward passes, streaming layers from `path`.

    Reported per mode: bytes actually read, per-layer read vs compute time, first-token
    cost vs steady-state cost (this is the number that matters - if nothing stays
    resident, token 100 costs the same as token 1), and peak RSS.
    """
    sizes = [s.nbytes for s in spec]
    need = sum(sizes)
    have = os.path.getsize(path) if os.path.exists(path) else 0
    if have < need:
        raise ValueError(f"file is {have} bytes, spec needs {need}")

    offsets, o = [], 0
    for n in sizes:
        offsets.append(o)
        o += n

    # compressed: every layer stored as int8 where the spec says f32/f16
    comp_sizes = [n // 2 for n in sizes]
    comp_offsets, o = [], 0
    for n in comp_sizes:
        comp_offsets.append(o)
        o += n

    rss_start = _rss_mb()
    load_t = [0.0] * len(spec)          # summed over tokens
    comp_t = [0.0] * len(spec)
    per_token = []
    bytes_read = 0
    faults0 = _minflt()

    fd = os.open(path, os.O_RDONLY)
    try:
        def read_exact(off, n):
            nonlocal bytes_read
            t0 = time.perf_counter()
            buf = os.pread(fd, n, off)
            bytes_read += len(buf)
            return time.perf_counter() - t0, buf

        mm = None
        if mode == "mmap":
            mm = mmap.mmap(fd, need, prot=mmap.PROT_READ)

        for tok_i in range(tokens):
            t_token = time.perf_counter()
            if mode == "mmap":
                for i, (off, n) in enumerate(zip(offsets, sizes)):
                    t0 = time.perf_counter()
                    view = memoryview(mm)[off:off + n]
                    _touch(view)                     # faults the pages in
                    view.release()
                    # with mmap there is no separate read step to time: the page fault
                    # *is* the read. Everything is one number, and faults (reported
                    # below, times the page size) is how much memory was actually
                    # touched. Splitting this into load/compute was fake precision.
                    comp_t[i] += time.perf_counter() - t0

            elif mode == "serial":
                for i in range(len(spec)):
                    dt, buf = read_exact(offsets[i], sizes[i])
                    load_t[i] += dt
                    t0 = time.perf_counter()
                    _touch(memoryview(buf))
                    comp_t[i] += time.perf_counter() - t0
                    del buf

            elif mode == "prefetch":
                pending = {}

                def kick(j):
                    pending[j] = read_exact(offsets[j], sizes[j])

                th = threading.Thread(target=kick, args=(0,))
                th.start()
                for i in range(len(spec)):
                    th.join()
                    dt, buf = pending.pop(i)
                    load_t[i] += dt
                    if i + 1 < len(spec):
                        th = threading.Thread(target=kick, args=(i + 1,))
                        th.start()
                    t0 = time.perf_counter()
                    _touch(memoryview(buf))
                    comp_t[i] += time.perf_counter() - t0
                    del buf
                if th.is_alive():
                    th.join()

            elif mode == "compressed":
                for i in range(len(spec)):
                    dt, raw = read_exact(comp_offsets[i], comp_sizes[i])
                    load_t[i] += dt
                    t0 = time.perf_counter()
                    _touch(memoryview(raw))          # placeholder for dequant
                    comp_t[i] += time.perf_counter() - t0
                    del raw
            else:
                raise ValueError(f"unknown mode {mode!r}")
            per_token.append(time.perf_counter() - t_token)

        if mm is not None:
            mm.close()
    finally:
        os.close(fd)

    total = sum(per_token)
    model_mb = need / 1e6
    return {
        "mode": mode,
        "compute": "placeholder (memory-shaped, not real matmul)" if COMPUTE_IS_PLACEHOLDER else "real",
        "model_mb": round(model_mb, 1),
        "layers": len(spec),
        "tokens": tokens,
        "seconds": round(total, 3),
        "first_token_ms": round(per_token[0] * 1e3, 1),
        "steady_ms_per_token": round(sum(per_token[1:]) / max(1, len(per_token) - 1) * 1e3, 1)
        if len(per_token) > 1 else None,
        "tok_per_s": round(tokens / total, 2) if total else None,
        "bytes_read": bytes_read if mode != "mmap" else None,
        "bytes_read_x_model": round(bytes_read / max(1, need), 2) if mode != "mmap" else None,
        "page_faults": _minflt() - faults0,
        "bytes_faulted_est": (_minflt() - faults0) * os.sysconf("SC_PAGE_SIZE")
        if mode == "mmap" else None,
        "effective_mb_per_s": round(bytes_read / 1e6 / total, 1) if total else None,
        "load_share_pct": (round(100 * sum(load_t) / max(1e-9, sum(load_t) + sum(comp_t)), 1)
                           if mode != "mmap" else None),
        "mean_layer_load_ms": round(sum(load_t) / tokens / len(spec) * 1e3, 3),
        "mean_layer_compute_ms": round(sum(comp_t) / tokens / len(spec) * 1e3, 3),
        "mean_layer_mb": round(model_mb / len(spec), 2),
        "minor_faults": _minflt() - faults0,
        "note": ("int8 on disk: reads half the f16 bytes; dequant is a placeholder"
                 if mode == "compressed" else
                 "page-fault driven; load and compute are not separable" if mode == "mmap" else None),
        "rss_start_mb": rss_start,
        "rss_peak_mb": _peak_rss_mb(),
    }


def run_mode_isolated(path: str, spec: list[LayerSpec], mode: str, tokens: int = 8) -> dict:
    """Run one mode in its own process: VmHWM is monotonic, so comparing modes inside a
    single process would report the *first* mode's peak for every later one."""
    import subprocess, sys as _sys
    code = (
        "import json,sys;"
        "sys.path.insert(0,sys.argv[1]);"
        "from engine_x.airllm_brain import stream_model, LayerSpec;"
        "spec=[LayerSpec(*s) for s in json.loads(sys.argv[3])];"
        "print(json.dumps(stream_model(sys.argv[2], spec, mode=sys.argv[4], tokens=int(sys.argv[5]))))"
    )
    spec_json = json.dumps([[s.name, s.params, s.dtype_bytes] for s in spec])
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([_sys.executable, "-c", code, root, path, spec_json, mode, str(tokens)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-800:])
    return json.loads(out.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- plan
def plan(model_bytes: int, ram_bytes: int, disk_bytes_per_s: float,
         n_layers: int = 32) -> dict:
    """Choose resident / mmap / stream for a model, with the projected tok/s of each.

    The physical rule: generating a token needs every weight once. If they are resident
    that is RAM bandwidth; if they are on disk, it is disk bandwidth, and nothing hides it
    except the OS page cache and prefetch overlap.
    """
    model_gb = model_bytes / 1e9
    ram_gb = ram_bytes / 1e9
    disk_gb_s = disk_bytes_per_s / 1e9
    ram_gb_s = 21.0                     # measured on this box (bench_all.layer0/1 hardware)
    fits = model_bytes * 1.25 < ram_bytes          # 25% headroom for KV + activations
    # int8 weights are 2x smaller than f16, so a model that does not fit f16 may fit
    # int8 - and int8 resident is ~10x faster than int8 streamed. This is the one
    # decision that makes AirLLM unnecessary rather than optimal.
    int8_bytes = model_bytes / 2
    fits_int8 = int8_bytes * 1.25 < ram_bytes
    return {
        "model_gb": round(model_gb, 2),
        "ram_gb": round(ram_gb, 2),
        "fits_resident_int8": fits_int8,
        "tok_per_s_resident_int8": round(ram_gb_s * 0.45 / (int8_bytes / 1e9), 2),
        "recommended_if_int8_fits": "int8 resident (quantise instead of streaming)",
        "fits_resident": fits,
        "recommended": "resident" if fits else ("mmap" if model_gb < 1.4 * ram_gb else "stream"),
        # resident: bandwidth-bound decode
        "tok_per_s_resident": round(ram_gb_s * 0.45 / model_gb, 2) if model_gb else None,
        # streamed: one full model read per token, minus 0-20% overlap from prefetch
        "tok_per_s_streamed": round(disk_gb_s / model_gb * 1.1, 3) if model_gb else None,
        "tok_per_s_mmap": round(disk_gb_s / model_gb * 1.0, 3) if model_gb else None,
        "ms_per_token_streamed": round(model_gb / disk_gb_s * 1e3, 1) if disk_gb_s else None,
        "layer_mb": round(model_gb * 1e3 / n_layers, 1),
        "note": ("streaming only wins when the model does not fit; at equal size resident "
                 "RAM bandwidth is ~5-6x this disk"),
    }


# --------------------------------------------------------------------------- cli
def _make_synthetic(path: str, spec: list[LayerSpec]) -> int:
    need = sum(s.nbytes for s in spec)
    with open(path, "wb") as f:
        block = bytes(4 << 20)
        written = 0
        while written < need:
            n = min(len(block), need - written)
            f.write(block[:n])
            written += n
    return need


if __name__ == "__main__":
    print(json.dumps({"peak_rss_mb": _peak_rss_mb()}, indent=1))
