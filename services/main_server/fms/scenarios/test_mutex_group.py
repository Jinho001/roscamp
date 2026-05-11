"""
mutex_group 단위테스트 (stdlib unittest).

실행:
    cd services/main_server
    python3 -m fms.scenarios.test_mutex_group
"""
import threading
import time
import unittest

from fms.scenarios.mutex_group import MutexGroupManager


class MutexGroupManagerTest(unittest.TestCase):

    def setUp(self):
        self.mgr = MutexGroupManager(preregister_from_zones=False)
        self.mgr.register("zone_a")
        self.mgr.register("zone_b")

    # ── 기본 동작 ────────────────────────────────────────────────────────

    def test_acquire_release_simple(self):
        self.assertTrue(self.mgr.acquire("zone_a", "r1"))
        self.assertEqual(self.mgr.status()["zone_a"]["owner"], "r1")
        self.assertTrue(self.mgr.status()["zone_a"]["locked"])

        self.mgr.release("zone_a", "r1")
        self.assertIsNone(self.mgr.status()["zone_a"]["owner"])
        self.assertFalse(self.mgr.status()["zone_a"]["locked"])

    def test_unknown_group_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.acquire("does_not_exist", "r1")

    def test_groups_independent(self):
        self.assertTrue(self.mgr.acquire("zone_a", "r1"))
        self.assertTrue(self.mgr.acquire("zone_b", "r2"))
        st = self.mgr.status()
        self.assertEqual(st["zone_a"]["owner"], "r1")
        self.assertEqual(st["zone_b"]["owner"], "r2")
        self.mgr.release("zone_a", "r1")
        self.mgr.release("zone_b", "r2")

    # ── 큐 / 블로킹 ──────────────────────────────────────────────────────

    def test_second_acquire_blocks_until_release(self):
        self.assertTrue(self.mgr.acquire("zone_a", "r1"))
        results: list[bool] = []

        def grab():
            results.append(self.mgr.acquire("zone_a", "r2", timeout=2.0))

        t = threading.Thread(target=grab)
        t.start()
        time.sleep(0.15)
        self.assertEqual(self.mgr.status()["zone_a"]["owner"], "r1")  # r2 아직 대기

        self.mgr.release("zone_a", "r1")
        t.join(timeout=2.0)
        self.assertEqual(results, [True])
        self.assertEqual(self.mgr.status()["zone_a"]["owner"], "r2")
        self.mgr.release("zone_a", "r2")

    def test_fifo_queue_order(self):
        self.assertTrue(self.mgr.acquire("zone_a", "r1"))
        completed: list[str] = []

        def grab_release(rid: str):
            if self.mgr.acquire("zone_a", rid, timeout=5.0):
                completed.append(rid)
                time.sleep(0.05)
                self.mgr.release("zone_a", rid)

        threads = []
        for rid in ("r2", "r3", "r4"):
            t = threading.Thread(target=grab_release, args=(rid,))
            t.start()
            threads.append(t)
            time.sleep(0.1)  # enqueue 순서 보장

        self.mgr.release("zone_a", "r1")
        for t in threads:
            t.join(timeout=5.0)

        self.assertEqual(completed, ["r2", "r3", "r4"])

    def test_acquire_timeout(self):
        self.assertTrue(self.mgr.acquire("zone_a", "r1"))
        start = time.time()
        ok = self.mgr.acquire("zone_a", "r2", timeout=0.3)
        elapsed = time.time() - start
        self.assertFalse(ok)
        self.assertGreaterEqual(elapsed, 0.3)
        self.assertLess(elapsed, 1.0)
        self.mgr.release("zone_a", "r1")

    def test_force_release_wakes_queue(self):
        self.assertTrue(self.mgr.acquire("zone_a", "r1"))
        result: list[bool] = []

        def grab():
            result.append(self.mgr.acquire("zone_a", "r2", timeout=2.0))

        t = threading.Thread(target=grab)
        t.start()
        time.sleep(0.1)

        self.mgr.force_release("zone_a")
        t.join(timeout=2.0)
        self.assertEqual(result, [True])
        self.mgr.release("zone_a", "r2")

    # ── zones.yaml 자동 등록 ─────────────────────────────────────────────

    def test_preregister_from_zones(self):
        mgr = MutexGroupManager(preregister_from_zones=True)
        groups = mgr.groups()
        self.assertIn("frontjet", groups)
        self.assertIn("warejet", groups)
        self.assertIn("subzone", groups)


if __name__ == "__main__":
    unittest.main(verbosity=2)
