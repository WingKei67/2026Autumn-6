import asyncio, time

async def worker(name, delay):
    await asyncio.sleep(delay)          # non-blocking: yields to the loop
    return f"{name} done"

async def main():
    t0 = time.perf_counter()
    done = await asyncio.gather(worker("feed", 3), worker("quote", 2), worker("order", 1))
    print(done, f"elapsed={time.perf_counter() - t0:.2f}s")

asyncio.run(main())