"""Reporter Agent — 生成检测报告和告警通知"""
import logging
import os
import json
from datetime import datetime
from core.base_agent import BaseAgent
from core.message import Message, MessageType

logger = logging.getLogger("ReviewGuard.Reporter")


class ReporterAgent(BaseAgent):
    """报告Agent：接收分析结果，生成报告，发送告警"""

    SUBSCRIBED_TOPICS = ["report_request", "alert"]

    def __init__(self, name: str, bus, llm_client=None, config: dict = None):
        super().__init__(name, bus)
        self.llm_client = llm_client  # LLM客户端（可选，用于生成自然语言报告）
        self.config = config or {}
        self.output_dir = self.config.get("output_dir", "data/reports")
        self.alert_levels = self.config.get("alert_levels", {
            "critical": 0.8,
            "warning": 0.5,
            "info": 0.0,
        })
        self._reports = []

        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)

    def run(self, **kwargs):
        """Reporter是被动触发"""
        pass

    def on_message(self, message: Message):
        if message.type == MessageType.RESULT:
            action = message.content.get("action")

            if action == "report":
                # 处理攻击事件报告
                events = message.content.get("attack_events", [])
                for event in events:
                    self._generate_attack_report(event)

            elif action == "routine_stats":
                # 常规统计报告
                stats = message.content.get("product_stats", [])
                self._generate_routine_report(stats)

        elif message.type == MessageType.ALERT:
            # 处理告警
            self._handle_alert(message.content)

        elif message.type == MessageType.QUERY:
            if message.content.get("action") == "recent_reports":
                self.send(message.sender, MessageType.RESULT, {
                    "reports": self._reports[-10:],
                })

    def _generate_attack_report(self, event: dict):
        """生成攻击事件报告"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pid = event.get("product_id", "unknown")
        severity = event.get("severity", "info")

        # 构建报告内容
        report = {
            "report_id": f"RPT_{timestamp}",
            "type": "attack_event",
            "severity": severity,
            "event": event,
            "generated_at": datetime.now().isoformat(),
        }

        # 如果有LLM，生成自然语言摘要
        if self.llm_client:
            report["llm_summary"] = self._llm_summarize(event)

        # 保存报告
        filename = f"attack_{pid}_{timestamp}.json"
        filepath = os.path.join(self.output_dir, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            report["filepath"] = filepath
            logger.info(f"[{self.name}] 攻击事件报告已生成: {filepath}")
        except Exception as e:
            logger.error(f"[{self.name}] 报告保存失败: {e}")

        # 生成Markdown格式报告
        md_report = self._event_to_markdown(event, report.get("llm_summary"))
        md_filename = f"attack_{pid}_{timestamp}.md"
        md_filepath = os.path.join(self.output_dir, md_filename)
        try:
            with open(md_filepath, "w", encoding="utf-8") as f:
                f.write(md_report)
            report["md_filepath"] = md_filepath
        except Exception as e:
            logger.error(f"[{self.name}] Markdown报告保存失败: {e}")

        self._reports.append(report)

        # 发送告警通知
        self._send_notification(severity, event)

    def _generate_routine_report(self, stats: list):
        """生成常规统计报告"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        report = {
            "report_id": f"RPT_{timestamp}",
            "type": "routine_stats",
            "severity": "info",
            "stats": stats,
            "generated_at": datetime.now().isoformat(),
        }

        filename = f"routine_{timestamp}.json"
        filepath = os.path.join(self.output_dir, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            logger.info(f"[{self.name}] 常规统计报告已生成: {filepath}")
        except Exception as e:
            logger.error(f"[{self.name}] 常规报告保存失败: {e}")

        self._reports.append(report)

    def _event_to_markdown(self, event: dict, llm_summary: str = None) -> str:
        """将攻击事件转为Markdown报告"""
        profile = event.get("profile", {})
        burst_info = event.get("burst_info", {})
        lines = [
            f"# 🚨 投毒攻击事件报告",
            f"",
            f"**事件ID**: {event.get('event_id', '')}",
            f"**检测时间**: {event.get('detection_time', '')}",
            f"**目标商品**: {event.get('product_id', '')}",
            f"**严重程度**: {event.get('severity', '').upper()}",
            f"",
            f"## 概览",
            f"",
            f"| 指标 | 数值 |",
            f"|------|------|",
            f"| 总评论数 | {event.get('total_reviews', 0)} |",
            f"| 虚假评论数 | {event.get('fake_count', 0)} |",
            f"| 虚假比例 | {event.get('fake_ratio', 0):.2%} |",
            f"",
            f"## 攻击画像",
            f"",
            f"- **攻击类型**: {profile.get('attack_type', '未知')}",
            f"- **评分分布**: {json.dumps(profile.get('score_distribution', {}), ensure_ascii=False)}",
            f"",
        ]

        # ---- 时间聚集检测 (Burst Detection) ----
        if burst_info.get("reviews_with_burst", 0) > 0:
            lines.extend([
                f"## ⏱️ 时间聚集检测 (Burst Detection)",
                f"",
                f"| 指标 | 数值 |",
                f"|------|------|",
                f"| 聚集评论数 | {burst_info.get('reviews_with_burst', 0)} |",
                f"| 聚集比例 | {burst_info.get('burst_ratio', 0):.2%} |",
                f"| 最高聚集分 | {burst_info.get('max_burst_score', 0):.2%} |",
                f"| 平均聚集分 | {burst_info.get('avg_burst_score', 0):.2%} |",
                f"",
            ])
            clusters = burst_info.get("clusters", [])
            if clusters:
                lines.append(f"**检测到 {len(clusters)} 个时间聚集簇：**")
                lines.append("")
                for ci, cluster in enumerate(clusters, 1):
                    lines.append(
                        f"- **簇{ci}**: {cluster.get('start_time', '?')} ~ {cluster.get('end_time', '?')}, "
                        f"共 {cluster.get('review_count', 0)} 条, "
                        f"平均聚集分 {cluster.get('avg_burst_score', 0):.2%}"
                    )
                    samples = cluster.get("sample_texts", [])
                    if samples:
                        for s in samples:
                            lines.append(f"  - \"{s}...\"")
                lines.append("")
            lines.append("> 时间聚集提示：上述评论在短时间内集中发布，符合批量刷单的行为模式。")
            lines.append("")

        if llm_summary:
            lines.extend([
                f"## AI分析摘要",
                f"",
                f"{llm_summary}",
                f"",
            ])

        timeline = profile.get("timeline", [])
        if timeline:
            lines.append("## 攻击时间线")
            lines.append("")
            for t in timeline[:10]:
                lines.append(f"- `{t.get('time', '')}` {t.get('text', '')} (置信度: {t.get('confidence', 0):.2f})")

        return "\n".join(lines)

    def _llm_summarize(self, event: dict) -> str:
        """调用LLM生成自然语言摘要"""
        if not self.llm_client:
            return ""

        prompt = (
            f"你是一个电商安全分析专家。请根据以下投毒攻击事件数据，生成一段简洁的中文分析摘要"
            f"（包括攻击类型、规模、可能影响和建议措施，200字以内）：\n\n"
            f"商品ID: {event.get('product_id', '')}\n"
            f"虚假评论数: {event.get('fake_count', 0)}\n"
            f"总评论数: {event.get('total_reviews', 0)}\n"
            f"虚假比例: {event.get('fake_ratio', 0):.2%}\n"
            f"攻击类型: {event.get('profile', {}).get('attack_type', '未知')}\n"
        )

        try:
            summary = self.llm_client.generate(prompt)
            return summary
        except Exception as e:
            logger.error(f"[{self.name}] LLM摘要生成失败: {e}")
            return ""

    def _send_notification(self, severity: str, event: dict):
        """发送告警通知（目前只打日志，后续可接入邮件/Webhook）"""
        alert_msg = (
            f"[ALERT-{severity.upper()}] 商品 {event.get('product_id')} "
            f"检测到投毒攻击, 假评比例 {event.get('fake_ratio', 0):.2%}"
        )
        logger.warning(f"[{self.name}] {alert_msg}")
        # TODO: 接入邮件或Webhook通知
        # if severity == "critical":
        #     self._send_email_alert(event)
        #     self._send_webhook_alert(event)

    def _handle_alert(self, content: dict):
        """处理来自其他Agent的告警"""
        alert_type = content.get("alert_type", "")
        logger.info(f"[{self.name}] 收到告警: {alert_type}")
