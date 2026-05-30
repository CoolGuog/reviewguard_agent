"""Agent基类 — 所有Agent的骨架"""
import logging
from abc import ABC, abstractmethod
from core.message import Message, MessageType

logger = logging.getLogger("ReviewGuard.Agent")


class BaseAgent(ABC):
    """Agent基类，定义统一接口和生命周期"""

    # 子类可以覆盖，声明自己订阅的topic
    SUBSCRIBED_TOPICS = []

    def __init__(self, name: str, bus):
        self.name = name
        self.bus = bus
        self.mailbox = []          # 收件箱
        self.state = "idle"        # idle / running / waiting / error
        self._error_count = 0      # 累计错误次数
        self._max_errors = 5       # 最大容忍错误次数

    def receive(self, message: Message):
        """收到消息，放入收件箱并触发处理"""
        self.mailbox.append(message)
        try:
            self.on_message(message)
        except Exception as e:
            self._error_count += 1
            logger.error(f"[{self.name}] 处理消息异常: {e}", exc_info=True)
            if self._error_count >= self._max_errors:
                self.state = "error"
                logger.critical(f"[{self.name}] 错误次数超限，Agent进入error状态")

    def send(self, receiver: str, msg_type: MessageType, content: dict):
        """发送消息给指定Agent"""
        msg = Message(
            type=msg_type,
            sender=self.name,
            receiver=receiver,
            content=content,
        )
        self.bus.send(msg)

    def send_alert(self, content: dict):
        """发送告警（广播给所有Agent）"""
        msg = Message(
            type=MessageType.ALERT,
            sender=self.name,
            content=content,
        )
        self.bus.broadcast(msg)

    @abstractmethod
    def on_message(self, message: Message):
        """收到消息后的处理逻辑，子类必须实现"""
        pass

    @abstractmethod
    def run(self, **kwargs):
        """主动执行任务（定时任务、初始化等），子类必须实现"""
        pass

    def status(self) -> dict:
        """返回Agent当前状态信息"""
        return {
            "name": self.name,
            "state": self.state,
            "mailbox_size": len(self.mailbox),
            "error_count": self._error_count,
        }
