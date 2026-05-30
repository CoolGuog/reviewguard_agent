"""Analyst Agent — 聚合分析，识别投毒攻击事件"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from core.base_agent import BaseAgent
from core.message import Message, MessageType

logger = logging.getLogger("ReviewGuard.Analyst")


class AnalystAgent(BaseAgent):
    """分析Agent：聚合检测结果，识别投毒攻击事件，生成攻击画像"""

    SUBSCRIBED_TOPICS = ["analysis_request"]

    def __init__(self, name: str, bus, config: dict = None):
        super().__init__(name, bus)
        self.config = config or {}
        self.time_window = self.config.get("time_window_minutes", 60)
        self.min_fake_count = self.config.get("min_fake_count", 5)
        self.fake_ratio_threshold = self.config.get("fake_ratio_threshold", 0.5)
        # 按商品存储历史检测结果
        self._product_reviews = defaultdict(list)  # product_id -> [review_results]
        self._attack_events = []                     # 已识别的攻击事件

    def run(self, **kwargs):
        """Analyst是被动触发，但也可以主动触发全局分析"""
        if kwargs.get("action") == "global_analysis":
            self._global_analysis()

    def on_message(self, message: Message):
        if message.type == MessageType.RESULT and message.content.get("action") == "analyze":
            results = message.content.get("results", [])
            if not results:
                return

            batch_id = message.content.get("crawl_batch_id", 0)
            burst_summary = message.content.get("burst_summary", {})
            logger.info(f"[{self.name}] 收到检测结果, 批次{batch_id}, {len(results)}条")

            # 按商品分组存储
            for r in results:
                pid = r.get("product_id", "unknown")
                self._product_reviews[pid].append(r)

            # 逐商品分析
            new_events = []
            analyzed_products = set(r.get("product_id", "unknown") for r in results)
            for pid in analyzed_products:
                event = self._detect_attack_event(pid, burst_summary)
                if event:
                    new_events.append(event)

            if new_events:
                # 发现攻击事件，发给Reporter
                self.send("reporter", MessageType.RESULT, {
                    "action": "report",
                    "attack_events": new_events,
                    "burst_summary": burst_summary,
                    "timestamp": datetime.now().isoformat(),
                })
                # 同时发告警
                self.send_alert({
                    "alert_type": "poison_attack_detected",
                    "events": new_events,
                })
            else:
                # 无攻击事件，发送常规统计
                self.send("reporter", MessageType.RESULT, {
                    "action": "routine_stats",
                    "product_stats": self._calc_product_stats(analyzed_products),
                    "burst_summary": burst_summary,
                    "timestamp": datetime.now().isoformat(),
                })

        elif message.type == MessageType.QUERY:
            if message.content.get("action") == "attack_events":
                self.send(message.sender, MessageType.RESULT, {
                    "attack_events": self._attack_events,
                })

    def _detect_attack_event(self, product_id: str, burst_summary: dict = None) -> dict | None:
        """
        检测单个商品是否存在投毒攻击事件
        逻辑：在时间窗口内，假评数量>=阈值 且 假评比例>=阈值
        """
        burst_summary = burst_summary or {}
        reviews = self._product_reviews.get(product_id, [])
        if len(reviews) < self.min_fake_count:
            return None

        # 按时间排序
        sorted_reviews = sorted(reviews, key=lambda r: r.get("time", ""))
        if not sorted_reviews:
            return None

        # 滑动时间窗口检测
        window_start = datetime.now() - timedelta(minutes=self.time_window)
        recent_reviews = []
        for r in sorted_reviews:
            try:
                r_time = datetime.fromisoformat(r.get("time", "").replace("Z", ""))
                if r_time >= window_start:
                    recent_reviews.append(r)
            except (ValueError, TypeError):
                recent_reviews.append(r)  # 时间解析失败也纳入

        if not recent_reviews:
            return None

        fake_reviews = [r for r in recent_reviews if r.get("label") == "fake"]
        fake_count = len(fake_reviews)
        fake_ratio = fake_count / len(recent_reviews) if recent_reviews else 0

        # 判断是否构成攻击事件
        if fake_count >= self.min_fake_count and fake_ratio >= self.fake_ratio_threshold:
            event = {
                "event_id": f"EVT_{product_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "product_id": product_id,
                "detection_time": datetime.now().isoformat(),
                "total_reviews": len(recent_reviews),
                "fake_count": fake_count,
                "fake_ratio": round(fake_ratio, 4),
                "severity": self._calc_severity(fake_ratio),
                # 攻击画像
                "profile": self._build_attack_profile(fake_reviews, product_id, burst_summary),
                # Burst 时间聚集信息
                "burst_info": {
                    "reviews_with_burst": burst_summary.get("reviews_with_burst", 0),
                    "burst_ratio": burst_summary.get("burst_ratio", 0),
                    "clusters": burst_summary.get("clusters", []),
                    "max_burst_score": burst_summary.get("max_burst_score", 0),
                    "avg_burst_score": burst_summary.get("avg_burst_score", 0),
                },
            }
            self._attack_events.append(event)
            logger.warning(
                f"[{self.name}] 检测到投毒攻击事件! 商品={product_id}, "
                f"假评={fake_count}/{len(recent_reviews)}, 比例={fake_ratio:.2%}"
            )
            return event

        return None

    def _build_attack_profile(self, fake_reviews: list, product_id: str, burst_summary: dict = None) -> dict:
        """构建攻击画像"""
        burst_summary = burst_summary or {}

        # 攻击手法判断
        score_dist = defaultdict(int)
        for r in fake_reviews:
            score = r.get("score", 0)
            if score >= 4:
                score_dist["刷单好评"] += 1
            elif score <= 2:
                score_dist["恶意差评"] += 1
            else:
                score_dist["中性评"] += 1

        dominant_type = max(score_dist, key=score_dist.get) if score_dist else "未知"

        # 攻击时间线
        timeline = []
        for r in fake_reviews:
            timeline.append({
                "time": r.get("time", r.get("comment_time", "")),
                "text": r.get("text", "")[:50],
                "confidence": r.get("confidence", 0),
                "burst_score": r.get("burst_score", 0),
            })

        profile = {
            "attack_type": dominant_type,
            "score_distribution": dict(score_dist),
            "timeline": timeline[:20],  # 最多保留20条
            "target_product": product_id,
        }

        # 附加时间聚集信息
        burst_clusters = burst_summary.get("clusters", [])
        if burst_clusters:
            profile["burst_clusters"] = burst_clusters
            profile["burst_review_count"] = burst_summary.get("reviews_with_burst", 0)

        return profile

    def _calc_severity(self, fake_ratio: float) -> str:
        """计算严重程度"""
        if fake_ratio >= 0.8:
            return "critical"
        elif fake_ratio >= 0.5:
            return "warning"
        else:
            return "info"

    def _calc_product_stats(self, product_ids: set) -> list:
        """计算各商品的检测统计"""
        stats = []
        for pid in product_ids:
            reviews = self._product_reviews.get(pid, [])
            fake = sum(1 for r in reviews if r.get("label") == "fake")
            stats.append({
                "product_id": pid,
                "total": len(reviews),
                "fake": fake,
                "real": len(reviews) - fake,
                "fake_ratio": round(fake / len(reviews), 4) if reviews else 0,
            })
        return stats

    def _global_analysis(self):
        """全局分析：对所有商品重新扫描"""
        for pid in list(self._product_reviews.keys()):
            self._detect_attack_event(pid)
