"""
inbound_demo_v2 구조/의존성 스모크 테스트 (stdlib unittest).

런타임 통합 테스트는 실제 fleet (RobotManager) 가 필요하므로 별도.
이 테스트는 모듈/상수/zones.yaml/mutex_groups 의존이 깨지지 않았는지만 확인.

실행:
    cd services/main_server
    python3 -m fms.scenarios.test_inbound_demo_v2
"""
import unittest

from fms.scenarios import inbound_demo_v2 as v2
from fms.scenarios.zones import load_zones, get_waypoint
from fms.scenarios.mutex_group import MutexGroupManager


class V2StructureTest(unittest.TestCase):

    def test_orchestrator_class_exists(self):
        self.assertTrue(hasattr(v2, "InboundDemoV2Orchestrator"))
        self.assertTrue(callable(v2.InboundDemoV2Orchestrator))

    def test_stage_constants(self):
        for c in (v2.DEMO_STAGE_V2_QUEUED, v2.DEMO_STAGE_V2_WAIT_LOCK,
                  v2.DEMO_STAGE_V2_MOVING, v2.DEMO_STAGE_V2_WORKING,
                  v2.DEMO_STAGE_V2_TO_HOME, v2.DEMO_STAGE_V2_DONE,
                  v2.DEMO_STAGE_V2_FAILED):
            self.assertIn(c, v2.DEMO_STAGE_V2_LABELS)
        # 50번대로 v1(40번대)와 분리되어야 함
        self.assertGreaterEqual(v2.DEMO_STAGE_V2_QUEUED, 50)
        self.assertLess(v2.DEMO_STAGE_V2_FAILED, 60)

    def test_legs_reference_zones(self):
        """LEGS / SUBZONE_LEG 의 zone 이름이 zones.yaml mutex 그룹과 일치."""
        zones = load_zones()
        groups = set(zones["by_mutex"].keys())

        for leg in v2.LEGS:
            self.assertIn(leg["zone"], groups, f"leg.zone {leg['zone']} 미등록")

        self.assertIn(v2.SUBZONE_LEG["zone"], groups)

    def test_legs_reference_waypoints(self):
        """LEGS / SUBZONE_LEG 의 waypoint 이름이 zones.yaml 에 모두 존재."""
        for leg in v2.LEGS:
            for key in ("approach", "center", "exit"):
                if leg.get(key) is None:
                    continue
                wp = get_waypoint(leg[key])  # KeyError 면 실패
                self.assertEqual(wp["name"], leg[key])

        for key in ("approach", "center"):
            wp = get_waypoint(v2.SUBZONE_LEG[key])
            self.assertEqual(wp["name"], v2.SUBZONE_LEG[key])

    def test_zone_waypoints_have_correct_mutex(self):
        """approach / center / exit 는 같은 zone mutex 에 묶여 있어야 한다."""
        for leg in v2.LEGS:
            zone = leg["zone"]
            for key in ("approach", "center", "exit"):
                if leg.get(key) is None:
                    continue
                wp = get_waypoint(leg[key])
                self.assertEqual(wp["mutex"], zone,
                                 f"{leg[key]} mutex={wp['mutex']} != {zone}")

    def test_holding_points_outside_mutex(self):
        """*_hold_0 들은 mutex 가 None 이어야 한다 (대기는 mutex 밖)."""
        zones = load_zones()
        for name, wp in zones["waypoints"].items():
            if wp["is_holding_point"]:
                self.assertIsNone(wp["mutex"],
                                  f"{name}: holding_point 인데 mutex={wp['mutex']}")

    def test_parking_spots_for_all_sshopy(self):
        """sshopy1/2/3 모두 parking_spot 등록되어 있어야 한다."""
        zones = load_zones()
        for rid in ("sshopy1", "sshopy2", "sshopy3"):
            self.assertIn(rid, zones["parking_by_owner"])

    def test_orchestrator_instantiable_with_fake_fleet(self):
        """실제 fleet 없이도 인스턴스화 가능해야 — Phase 4 단위테스트의 토대."""
        class FakeFleet:
            _states = {}
            _SCRIPTS = {}

        orch = v2.InboundDemoV2Orchestrator(FakeFleet())
        self.assertFalse(orch.is_active())
        st = orch.get_status()
        self.assertFalse(st["active"])
        self.assertEqual(st["robots"], {})
        # mutex_groups status 는 비어있어도 dict 형태
        self.assertIsInstance(st["mutex_groups"], dict)

    def test_mutex_groups_singleton_has_zones(self):
        """프로덕션 싱글턴이 zones.yaml 에서 frontjet/warejet/subzone 등록했는지."""
        from fms.scenarios.mutex_group import mutex_groups
        for g in ("frontjet", "warejet", "subzone"):
            self.assertIn(g, mutex_groups.groups())


if __name__ == "__main__":
    unittest.main(verbosity=2)
