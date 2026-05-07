#!/usr/bin/env bash
# domain_bridge로 각 로봇 도메인의 토픽을 서버 도메인(99)으로 브릿지.
# 서버용 rosbridge 1개(port 9090, domain 99)도 함께 시작.
#
# 토폴로지:
#   sshopy1 (domain 11) ─┐
#   sshopy2 (domain 12) ─┤ domain_bridge ─→ 서버 (domain 99, rosbridge:9090)
#   sshopy3 (domain 13) ─┤                   /sshopy1/odom, /sshopy2/odom ...
#   front_jet (domain 14)┤
#   ware_jet (domain 15) ┘

set -eo pipefail

# venv 제거 (ROS2가 시스템 Python을 쓰도록)
if [ -n "${VIRTUAL_ENV:-}" ]; then
    PATH="${PATH/$VIRTUAL_ENV\/bin:/}"
    unset VIRTUAL_ENV VIRTUAL_ENV_PROMPT
fi

source /opt/ros/jazzy/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_DIR="$SCRIPT_DIR/bridge_configs"

export CYCLONEDDS_URI="file://$SCRIPT_DIR/cyclonedds.xml"

# 기존 프로세스 정리
pkill -f "domain_bridge/domain_bridge" 2>/dev/null || true
pkill -f "rosbridge_websocket" 2>/dev/null || true
sleep 1

# domain_bridge 시작 (로봇별 2개: robot→server + server→robot)
declare -a ROBOTS=(sshopy1 sshopy2 sshopy3 front_jet ware_jet)

for robot in "${ROBOTS[@]}"; do
    # Robot → Server (토픽 수신)
    config="$BRIDGE_DIR/${robot}_bridge.yaml"
    if [ -f "$config" ]; then
        echo "Starting domain_bridge for $robot (robot→server) ..."
        ros2 run domain_bridge domain_bridge "$config" \
            > "/tmp/bridge_${robot}.log" 2>&1 &
    fi
    # Server → Robot (goal_pose, cmd_vel 발행)
    rev_config="$BRIDGE_DIR/${robot}_to_robot.yaml"
    if [ -f "$rev_config" ]; then
        echo "Starting domain_bridge for $robot (server→robot) ..."
        ros2 run domain_bridge domain_bridge "$rev_config" \
            > "/tmp/bridge_${robot}_rev.log" 2>&1 &
    fi
done

# 서버용 rosbridge (domain 99, port 9090)
echo "Starting rosbridge (domain 99, port 9090) ..."
ROS_DOMAIN_ID=99 ros2 launch rosbridge_server \
    rosbridge_websocket_launch.xml port:=9090 address:=0.0.0.0 \
    > /tmp/rosbridge_d99.log 2>&1 &

echo ""
echo "Waiting 8s for startup..."
sleep 8

echo "Status:"
bridge_count=$(pgrep -c -f "domain_bridge" 2>/dev/null || echo 0)
echo "  domain_bridge processes: $bridge_count"
rosbridge_result=$(nc -zv localhost 9090 -w 2 2>&1 | grep -oE "succeeded|refused" || echo "unknown")
echo "  rosbridge (port 9090): $rosbridge_result"

echo ""
echo "Verify with: ROS_DOMAIN_ID=99 ros2 topic list"
