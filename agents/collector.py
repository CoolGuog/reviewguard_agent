"""Collector Agent — 采集电商评论"""
import logging
import random
import time
from datetime import datetime
from core.base_agent import BaseAgent
from core.message import Message, MessageType

logger = logging.getLogger("ReviewGuard.Collector")


class CollectorAgent(BaseAgent):
    """采集Agent：定时爬取电商平台评论，发送给Detector"""

    SUBSCRIBED_TOPICS = ["crawl_request", "recrawl"]

    def __init__(self, name: str, bus, crawler, config: dict = None):
        super().__init__(name, bus)
        self.crawler = crawler
        self.config = config or {}
        self._crawl_count = 0  # 累计采集次数

    def run(self, product_ids: list = None, **kwargs):
        """主动执行采集任务"""
        self.state = "running"
        try:
            product_ids = product_ids or self.config.get("product_ids", [])
            if not product_ids:
                logger.warning(f"[{self.name}] 没有指定商品ID，跳过采集")
                return

            all_reviews = []
            for pid in product_ids:
                logger.info(f"[{self.name}] 开始采集商品 {pid} 的评论")
                reviews = self._crawl_product(pid)
                all_reviews.extend(reviews)
                # 随机延时，避免被封
                delay = random.uniform(*self.config.get("delay_range", (1, 3)))
                time.sleep(delay)

            if all_reviews:
                self._crawl_count += 1
                logger.info(
                    f"[{self.name}] 采集完成, 共{len(all_reviews)}条评论, "
                    f"发送给detector"
                )
                # 发送给Detector
                self.send("detector", MessageType.TASK, {
                    "action": "detect",
                    "reviews": all_reviews,
                    "crawl_batch_id": self._crawl_count,
                    "timestamp": datetime.now().isoformat(),
                })
            else:
                logger.warning(f"[{self.name}] 未采集到任何评论")

        except Exception as e:
            logger.error(f"[{self.name}] 采集异常: {e}", exc_info=True)
            self.state = "error"
            raise
        finally:
            if self.state == "running":
                self.state = "idle"

    def _crawl_product(self, product_id: str) -> list:
        """爬取单个商品的评论"""
        max_pages = self.config.get("max_pages", 10)
        try:
            reviews = self.crawler.fetch_reviews(product_id, max_pages=max_pages)
            return reviews
        except Exception as e:
            logger.error(f"[{self.name}] 爬取商品{product_id}失败: {e}")
            return []

    def on_message(self, message: Message):
        """处理其他Agent发来的消息"""
        if message.type == MessageType.QUERY:
            action = message.content.get("action")
            if action == "recrawl":
                # 重新采集指定商品
                pid = message.content.get("product_id")
                if pid:
                    self.run(product_ids=[pid])
            elif action == "status":
                # 返回采集状态
                self.send(message.sender, MessageType.RESULT, {
                    "crawl_count": self._crawl_count,
                    "state": self.state,
                })

        elif message.type == MessageType.TASK:
            # 来自外部调度，可能是指定采集任务
            product_ids = message.content.get("product_ids")
            if product_ids:
                self.run(product_ids=product_ids)
