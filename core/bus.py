"""消息总线模块 — Agent间通信的核心"""
import logging
from collections import defaultdict
from core.message import Message

logger = logging.getLogger("ReviewGuard.Bus")


class MessageBus:
    """消息总线，支持点对点发送和发布订阅两种模式"""

    def __init__(self):
        self.agents = {}                              # name -> agent实例
        self.subscriptions = defaultdict(list)         # topic -> [agent_names]
        self._history = []                             # 消息历史（调试用）

    def register(self, agent):
        """注册Agent到总线"""
        self.agents[agent.name] = agent
        logger.info(f"[Bus] Agent注册: {agent.name}")

    def unregister(self, agent_name: str):
        """移除Agent"""
        if agent_name in self.agents:
            del self.agents[agent_name]
            logger.info(f"[Bus] Agent移除: {agent_name}")

    # ---- 点对点 ----
    def send(self, message: Message):
        """点对点发送：指定receiver"""
        receiver = self.agents.get(message.receiver)
        if not receiver:
            logger.warning(f"[Bus] 目标Agent不存在: {message.receiver}")
            return False
        self._history.append(message)
        logger.debug(f"[Bus] {message.sender} -> {message.receiver}: {message.type.value}")
        receiver.receive(message)
        return True

    def broadcast(self, message: Message):
        """广播给所有Agent（除发送者）"""
        self._history.append(message)
        for name, agent in self.agents.items():
            if name != message.sender:
                agent.receive(message)

    # ---- 发布订阅 ----
    def subscribe(self, topic: str, agent_name: str):
        """订阅主题"""
        if agent_name not in self.subscriptions[topic]:
            self.subscriptions[topic].append(agent_name)
            logger.info(f"[Bus] {agent_name} 订阅了主题: {topic}")

    def unsubscribe(self, topic: str, agent_name: str):
        """取消订阅"""
        if agent_name in self.subscriptions.get(topic, []):
            self.subscriptions[topic].remove(agent_name)

    def publish(self, topic: str, message: Message):
        """发布到主题，所有订阅者收到"""
        self._history.append(message)
        subscribers = self.subscriptions.get(topic, [])
        logger.debug(f"[Bus] 发布到 {topic}, 订阅者: {subscribers}")
        for agent_name in subscribers:
            agent = self.agents.get(agent_name)
            if agent:
                agent.receive(message)

    # ---- 辅助 ----
    def get_history(self, limit: int = 50):
        """获取最近的消息历史"""
        return self._history[-limit:]

    def get_agent_names(self):
        """获取所有已注册的Agent名称"""
        return list(self.agents.keys())
