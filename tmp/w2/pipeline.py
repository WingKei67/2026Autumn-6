import asyncio, time

TICKS = 2000

async def feed(q: asyncio.Queue, n: int) -> int:
    """The producer: mimics book snapshots arriving off a socket."""
    for i in range(n):
        await q.put({"seq": i, "sent": time.perf_counter()})
        await asyncio.sleep(0)          # let the consumer run
    await q.put(None)                   # sentinel = clean shutdown
    return n

async def strategy(q: asyncio.Queue, blocking: bool) -> tuple[int, float, int]:
    """The consumer: one 'decision' per tick, 1 ms of work."""
    seen, worst, peak = 0, 0.0, 0
    while True:
        item = await q.get()
        peak = max(peak, q.qsize())
        if item is None:
            q.task_done()
            break
        worst = max(worst, time.perf_counter() - item["sent"])
        if blocking:
            time.sleep(0.001)           # WRONG: blocks the whole loop
        else:
            await asyncio.sleep(0.001)  # RIGHT: yields
        seen += 1
        q.task_done()
    return seen, worst, peak

async def main(maxsize: int, blocking: bool = False) -> dict:
    q = asyncio.Queue(maxsize=maxsize)
    t0 = time.perf_counter()
    produced, (consumed, worst, peak) = await asyncio.gather(
        feed(q, TICKS), strategy(q, blocking))
    return {"maxsize": maxsize, "produced": produced, "consumed": consumed,
            "peak_depth": peak, "worst_lag_ms": round(worst * 1e3, 1),
            "elapsed_s": round(time.perf_counter() - t0, 2)}

for size in (1, 10_000):
    print(asyncio.run(main(size)))