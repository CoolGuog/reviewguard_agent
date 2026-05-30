"""Detector Agent — 虚假评论检测（RoBERTa模型 + Burst时间聚类检测）"""
import logging
from datetime import datetime
from core.base_agent import BaseAgent
from core.message import Message, MessageType
from core.burst_detector import BurstDetector

logger = logging.getLogger("ReviewGuard.Detector")


class DetectorAgent(BaseAgent):
    """
    检测Agent：接收评论列表，先逐条模型推理，再横向时间聚类检测，
    融合两者分数输出最终标签+置信度。

    流程：
    1. 逐条模型预测 → 获得 model_confidence（仅基于文本+元数据）
    2. 批量时间聚类检测 → 获得 burst_score（仅基于 comment_time）
    3. 融合: final_confidence = (1-burst_weight) * model_confidence + burst_weight * burst_score
    """

    SUBSCRIBED_TOPICS = ["detect_request"]

    def __init__(self, name: str, bus, model, config: dict = None):
        super().__init__(name, bus)
        self.model = model  # 外部注入的检测模型
        self.config = config or {}
        self.confidence_threshold = self.config.get("confidence_threshold", 0.6)

        # 初始化 Burst 检测器
        self.burst_detector = BurstDetector(
            window_minutes=self.config.get("burst_window_minutes", 30),
            min_cluster_size=self.config.get("burst_min_cluster_size", 3),
        )
        self.burst_weight = self.config.get("burst_weight", 0.25)

        self._total_detected = 0
        self._fake_count = 0

    def run(self, **kwargs):
        """Detector是被动触发，不需要主动run"""
        pass

    def on_message(self, message: Message):
        if message.type == MessageType.TASK and message.content.get("action") == "detect":
            reviews = message.content.get("reviews", [])
            if not reviews:
                logger.warning(f"[{self.name}] 收到空评论列表")
                return

            batch_id = message.content.get("crawl_batch_id", 0)
            logger.info(f"[{self.name}] 开始检测, 批次{batch_id}, 共{len(reviews)}条")

            results = self._batch_detect(reviews)

            self._total_detected += len(reviews)
            fake_in_batch = sum(1 for r in results if r["label"] == "fake")
            self._fake_count += fake_in_batch

            # 统计 burst 信息
            burst_hits = sum(1 for r in results if r.get("burst_score", 0) > 0)
            if burst_hits > 0:
                logger.info(
                    f"[{self.name}] Burst聚类检测: {burst_hits}/{len(reviews)} 条处于时间聚集簇"
                )

            logger.info(
                f"[{self.name}] 检测完成, 假评{fake_in_batch}/{len(reviews)}条, "
                f"累计假评{self._fake_count}/{self._total_detected}"
            )

            # 生成 burst 摘要
            burst_summary = self.burst_detector.get_cluster_summary(
                reviews, [r.get("burst_score", 0.0) for r in results]
            )

            # 发送给Analyst
            self.send("analyst", MessageType.RESULT, {
                "action": "analyze",
                "results": results,
                "crawl_batch_id": batch_id,
                "burst_summary": burst_summary,
                "timestamp": datetime.now().isoformat(),
            })

        elif message.type == MessageType.QUERY:
            if message.content.get("action") == "status":
                self.send(message.sender, MessageType.RESULT, {
                    "total_detected": self._total_detected,
                    "fake_count": self._fake_count,
                    "state": self.state,
                })

    def _batch_detect(self, reviews: list) -> list:
        """
        批量检测评论：模型推理 + 时间聚类融合

        对每条评论:
        1. 调用模型 predict(text, metadata) 获得 model_label 和 model_confidence
        2. 调用 BurstDetector 批量计算 burst_score
        3. 若有 burst_score > 0，融合模型分与 burst 分
        4. 融合后置信度超过阈值则可能翻转标签
        """
        n = len(reviews)

        # --- Step 1: 逐条模型预测 ---
        model_results = []
        for r in reviews:
            try:
                label, confidence = self.model.predict(
                    text=r.get("text", ""),
                    metadata={
                        "username": r.get("username", ""),
                        "comment_time": r.get("comment_time", ""),
                        "score": r.get("score", 0),
                    },
                )
                model_results.append({
                    "model_label": label,
                    "model_confidence": confidence,
                })
            except Exception as e:
                logger.error(f"[{self.name}] 单条检测失败: {e}")
                model_results.append({
                    "model_label": "unknown",
                    "model_confidence": 0.0,
                })

        # --- Step 2: 时间聚类检测 ---
        burst_scores = self.burst_detector.detect(reviews)

        # --- Step 3: 融合分数（Burst 只增加嫌疑，不降低） ---
        results = []
        for i, r in enumerate(reviews):
            mr = model_results[i]
            burst = burst_scores[i]

            model_conf = mr["model_confidence"]
            model_label = mr["model_label"]

            # 融合：burst 只增加嫌疑，不影响原本低疑评论
            if burst > 0:
                # boost 公式：burst 权重的提升量 = burst_weight * burst * (1 - model_conf)
                # burst 越强、模型越不确定时，提升越大
                boost = self.burst_weight * burst * (1 - model_conf)
                final_conf = round(min(model_conf + boost, 1.0), 4)
                # burst 提升后可能翻转标签
                if model_label == "real" and final_conf > 0.5:
                    final_label = "fake"
                else:
                    final_label = model_label
            else:
                final_conf = model_conf
                final_label = model_label

            # 置信度低于阈值的标记为待审核
            review_status = (
                "confirmed" if final_conf >= self.confidence_threshold else "review_needed"
            )

            results.append({
                **r,
                "label": final_label,
                "confidence": final_conf,
                "model_confidence": model_conf,
                "burst_score": round(burst, 4),
                "model_label": model_label,
                "review_status": review_status,
            })

        return results
