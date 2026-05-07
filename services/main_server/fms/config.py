# [전체주석] 아래 파일 전체에 코드 이해를 위한 주석 추가됨
"""
Robot fleet configuration.

domain_bridge topology:
  서버 (domain 99, rosbridge port 9090)
  sshopy1  → domain 11, namespace "sshopy1"
  sshopy2  → domain 12, namespace "sshopy2"
  sshopy3  → domain 13, namespace "sshopy3"
  front_jet → domain 14, namespace "front_jet"
  ware_jet  → domain 15, namespace "ware_jet"

domain_bridge가 각 로봇 도메인의 토픽을 서버 도메인(99)으로 브릿지.
FMS는 단일 rosbridge(localhost:9090, domain 99)에 연결하여
/{namespace}/topic 형태로 모든 로봇 토픽을 구독/발행한다.
"""

# [전체주석] 서버 도메인 및 rosbridge 설정
SERVER_DOMAIN_ID = 99
ROSBRIDGE_HOST = "localhost"
ROSBRIDGE_PORT = 9090

# [전체주석] 로봇 ID → 설정 매핑 dict.
#            namespace: domain 99에서의 토픽 접두사 (domain_bridge가 remap)
ROBOTS: dict[str, dict] = {
    "sshopy1": {
        "host": ROSBRIDGE_HOST, "port": ROSBRIDGE_PORT,
        "type": "pinky",
        "domain_id": 11,
        "namespace": "sshopy1",
    },
    "sshopy2": {
        "host": ROSBRIDGE_HOST, "port": ROSBRIDGE_PORT,
        "type": "pinky",
        "domain_id": 12,
        "namespace": "sshopy2",
    },
    "sshopy3": {
        "host": ROSBRIDGE_HOST, "port": ROSBRIDGE_PORT,
        "type": "pinky",
        "domain_id": 13,
        "namespace": "sshopy3",
    },
    "front_jet": {
        "host": ROSBRIDGE_HOST, "port": ROSBRIDGE_PORT,
        "type": "jetcobot",
        "domain_id": 14,
        "namespace": "front_jet",
        "joint_topic": "/front_jet/joint_states",
        "ssh_host": "192.168.1.114",
        "ssh_user": "jetcobot",
        "ssh_pass": "1",
    },
    "ware_jet": {
        "host": ROSBRIDGE_HOST, "port": ROSBRIDGE_PORT,
        "type": "jetcobot",
        "domain_id": 15,
        "namespace": "ware_jet",
        "joint_topic": "/ware_jet/joint_states",
        "ssh_host": "192.168.1.115",
        "ssh_user": "jetcobot",
        "ssh_pass": "1",
    },
}
