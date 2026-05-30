"""调度中心 — 管理Agent生命周期和工作流"""
import threading
import time
import logging
from core.bus import MessageBus
from core.message import Message, MessageType

logger = logging.getLogger("ReviewGuard.Orchestrator")


class Orchestrator:
    """调度中心：注册Agent、启动流水线、定时任务"""

    def __init__(self):
        self.bus = MessageBus()
        self.workflow = []           # 工作流定义
        self._running = False
        self._periodic_thread = None

    def add_agent(self, agent):
        """注册Agent到总线，并自动订阅其声明的topics"""
        self.bus.register(agent)
        for topic in agent.SUBSCRIBED_TOPICS:
            self.bus.subscribe(topic, agent.name)

    def define_workflow(self, steps: list):
        """
        定义默认工作流
        steps: [('collector', 'detector'), ('detector', 'analyst'), ...]
        """
        self.workflow = steps
        logger.info(f"[Orchestrator] 工作流定义: {steps}")

    def start_pipeline(self, initial_data: dict = None):
        """启动一次完整流水线"""
        if not self.bus.agents:
            logger.error("[Orchestrator] 没有注册任何Agent")
            return

        initial_data = initial_data or {}
        logger.info(f"[Orchestrator] 启动流水线, 参数: {initial_data}")

        # 触发第一个Agent（Collector）执行采集
        collector = self.bus.agents.get("collector")
        if collector:
            try:
                collector.run(**initial_data)
            except Exception as e:
                logger.error(f"[Orchestrator] Collector执行失败: {e}")
        else:
            logger.warning("[Orchestrator] 未找到collector Agent")

    def start_periodic(self, interval_seconds: int, initial_data: dict = None):
        """启动定时流水线（后台线程）"""
        self._running = True
        initial_data = initial_data or {}

        def _loop():
            logger.info(f"[Orchestrator] 定时任务启动, 间隔{interval_seconds}秒")
            while self._running:
                try:
                    self.start_pipeline(initial_data)
                except Exception as e:
                    logger.error(f"[Orchestrator] 定时任务异常: {e}")
                time.sleep(interval_seconds)

        self._periodic_thread = threading.Thread(target=_loop, daemon=True)
        self._periodic_thread.start()

    def stop_periodic(self):
        """停止定时任务"""
        self._running = False
        logger.info("[Orchestrator] 定时任务已停止")

    def get_status(self) -> dict:
        """获取系统状态"""
        agents_status = {}
        for name, agent in self.bus.agents.items():
            agents_status[name] = agent.status()
        return {
            "running": self._running,
            "agents": agents_status,
            "workflow": self.workflow,
            "bus_history_size": len(self.bus._history),
        }
