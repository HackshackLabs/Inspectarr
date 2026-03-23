"""Activity cache behavior tests."""

import asyncio
import unittest

from tautulli_inspector.activity_cache import ActivitySnapshotCache


class ActivitySnapshotCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_returns_fresh_hit_within_ttl(self) -> None:
        cache = ActivitySnapshotCache(ttl_seconds=1.0, stale_seconds=1.0)
        calls = 0

        async def fetcher() -> dict:
            nonlocal calls
            calls += 1
            return {"calls": calls}

        payload_1, state_1, _ = await cache.get(fetcher)
        payload_2, state_2, _ = await cache.get(fetcher)

        self.assertEqual(1, calls)
        self.assertEqual("miss", state_1)
        self.assertEqual("hit_fresh", state_2)
        self.assertEqual(payload_1, payload_2)

    async def test_cache_serves_stale_and_revalidates_in_background(self) -> None:
        cache = ActivitySnapshotCache(ttl_seconds=0.01, stale_seconds=1.0)
        calls = 0

        async def fetcher() -> dict:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return {"calls": calls}

        await cache.get(fetcher)
        await asyncio.sleep(0.02)
        payload_stale, state_stale, _ = await cache.get(fetcher)
        self.assertEqual("hit_stale", state_stale)
        self.assertEqual(1, payload_stale["calls"])

        await asyncio.sleep(0.05)
        payload_fresh, state_fresh, _ = await cache.get(fetcher)
        self.assertIn(state_fresh, ("hit_fresh", "hit_stale"))
        self.assertGreaterEqual(payload_fresh["calls"], 2)

    async def test_schedule_retry_forces_refresh_after_delay(self) -> None:
        cache = ActivitySnapshotCache(ttl_seconds=60.0, stale_seconds=1.0)
        calls = 0

        async def fetcher() -> dict:
            nonlocal calls
            calls += 1
            return {"calls": calls}

        await cache.get(fetcher)
        self.assertEqual(1, calls)

        cache.schedule_retry(fetcher, retry_after_seconds=0.02)
        countdown = cache.retry_countdown_seconds()
        self.assertIsNotNone(countdown)
        await asyncio.sleep(0.05)

        payload, _, _ = await cache.get(fetcher)
        self.assertGreaterEqual(calls, 2)
        self.assertGreaterEqual(payload["calls"], 2)

    async def test_timeout_backoff_sequence_30_60_120_then_reset(self) -> None:
        cache = ActivitySnapshotCache(ttl_seconds=60.0, stale_seconds=1.0)

        async def fetcher() -> dict:
            return {"ok": True}

        await cache.get(fetcher)
        d1 = cache.update_timeout_retry_state(True, 30.0)
        self.assertEqual(30.0, d1)
        # Same snapshot should not increment.
        d1b = cache.update_timeout_retry_state(True, 30.0)
        self.assertEqual(30.0, d1b)

        await cache._refresh(fetcher, force=True)  # new snapshot
        d2 = cache.update_timeout_retry_state(True, 30.0)
        self.assertEqual(60.0, d2)

        await cache._refresh(fetcher, force=True)  # new snapshot
        d3 = cache.update_timeout_retry_state(True, 30.0)
        self.assertEqual(120.0, d3)

        await cache._refresh(fetcher, force=True)  # stays capped
        d4 = cache.update_timeout_retry_state(True, 30.0)
        self.assertEqual(120.0, d4)

        reset = cache.update_timeout_retry_state(False, 30.0)
        self.assertIsNone(reset)


if __name__ == "__main__":
    unittest.main()
