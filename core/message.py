"""消息定义模块"""
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import uuid


class MessageType(Enum):
    TASK = "task"           # 分发任务
    RESULT = "result"       # 返回结果
    ALERT = "alert"         # 告警
    QUERY = "query"         # 查询
    ERROR = "error"         # 错误


@dataclass
class Message:
    """Agent间通信的消息体"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: MessageType = MessageType.TASK
    sender: str = ""
    receiver: str = ""
    content: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __repr__(self):
        return (
            f"Message(id={self.id}, {self.sender}->{self.receiver}, "
            f"type={self.type.value}, content_keys={list(self.content.keys())})"
        )
