"""시나리오 Task 데이터클래스."""
import time
from dataclasses import dataclass, field
from typing import Optional

from fms.scenarios.constants import (
    INBOUND_STAGE_TO_FRONTJET, INBOUND_STAGE_LABELS,
    RETRIEVAL_STAGE_TO_ENTRANCE, RETRIEVAL_STAGE_LABELS,
)


@dataclass
class InboundItem:
    """입고 대상 상품 1건."""
    product_id: str
    size: int = 0
    color: str = ""
    quantity: int = 1


@dataclass
class InboundTask:
    """입고 task 1건의 상태."""
    task_id: str
    robot_id: str
    stage: int = INBOUND_STAGE_TO_FRONTJET
    items: list = field(default_factory=list)
    scan_result: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    stage_started_at: float = field(default_factory=time.time)
    completed: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "robot_id": self.robot_id,
            "stage": self.stage,
            "stage_label": INBOUND_STAGE_LABELS.get(
                self.stage, "완료" if self.completed else "알 수 없음"
            ),
            "items": [
                {"product_id": i.product_id, "size": i.size,
                 "color": i.color, "quantity": i.quantity}
                for i in self.items
            ],
            "scan_result": self.scan_result,
            "elapsed": round(time.time() - self.created_at, 1),
            "completed": self.completed,
            "error": self.error,
        }


@dataclass
class RetrievalTask:
    """회수 task 1건의 상태."""
    task_id: str
    robot_id: str
    stage: int = RETRIEVAL_STAGE_TO_ENTRANCE
    product_id: Optional[str] = None
    product_info: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    stage_started_at: float = field(default_factory=time.time)
    completed: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "robot_id": self.robot_id,
            "stage": self.stage,
            "stage_label": RETRIEVAL_STAGE_LABELS.get(
                self.stage, "완료" if self.completed else "알 수 없음"
            ),
            "product_id": self.product_id,
            "product_info": self.product_info,
            "elapsed": round(time.time() - self.created_at, 1),
            "completed": self.completed,
            "error": self.error,
        }
