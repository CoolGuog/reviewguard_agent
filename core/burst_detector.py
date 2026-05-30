"""
Burst Detector — 评论时间聚类检测模块

仅使用 comment_time 检测批量刷评行为：
- 横向比较多条评论的发布时间，找出时间上高度聚集的评论群
- 不需要 order_time，不依赖下单-评论间隔
- 当无时间信息时自动降级，所有评论 burst_score = 0

算法：滑动窗口聚类
1. 提取所有评论的 comment_time，按时间排序
2. 对每条评论，统计 ±window_minutes 内有多少条其他评论
3. 聚集密度 ≥ min_cluster_size 的评论标记为"刷评嫌疑簇"
4. 输出每条评论的 burst_score（0.0~1.0）
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("ReviewGuard.BurstDetector")


class BurstDetector:
    """
    评论时间聚类检测器

    Attributes:
        window_minutes: 时间窗口大小（分钟），默认30分钟
        min_cluster_size: 最小聚集数量，窗口内≥N条评论视为聚集
    """

    def __init__(self, window_minutes: int = 30, min_cluster_size: int = 3):
        self.window_minutes = window_minutes
        self.min_cluster_size = min_cluster_size

    def detect(self, reviews: list) -> list:
        """
        检测评论列表中的时间聚集现象

        Args:
            reviews: 评论列表，每条需包含 comment_time 字段
                     格式: {"comment_time": "2025-06-15 01:24:18", ...}

        Returns:
            burst_scores: 与输入等长的列表，每个元素为 0.0~1.0 的聚集分数
                         0.0 = 无聚集，1.0 = 高密度聚集
        """
        n = len(reviews)
        if n == 0:
            return []

        # --- 解析时间 ---
        timestamps = []
        parseable_indices = []

        for i, r in enumerate(reviews):
            ct_str = r.get("comment_time", "")
            parsed = self._parse_time(ct_str)
            if parsed is not None:
                timestamps.append(parsed)
                parseable_indices.append(i)

        # 无法解析任何时间 → 全部返回 0
        if not timestamps:
            logger.info("[BurstDetector] 无有效 comment_time，跳过聚类检测")
            return [0.0] * n

        # --- 按时间排序 ---
        sorted_pairs = sorted(
            zip(parseable_indices, timestamps), key=lambda x: x[1]
        )
        sorted_indices = [p[0] for p in sorted_pairs]
        sorted_times = [p[1] for p in sorted_pairs]
        m = len(sorted_times)

        # --- 滑动窗口计算每条评论的邻居数 ---
        window = timedelta(minutes=self.window_minutes)
        neighbor_counts = [0] * m

        # 双指针滑动窗口
        left = 0
        for right in range(m):
            # 右边界向右扩展，左边界收缩以保持窗口范围
            while left < right and (
                sorted_times[right] - sorted_times[left]
            ) > window:
                left += 1
            # 窗口内的评论数（不含自身）
            neighbor_counts[right] = right - left

        # 同样从右向左扫描，覆盖每条评论作为窗口中心的情况
        right = m - 1
        for left in range(m - 1, -1, -1):
            while right > left and (
                sorted_times[right] - sorted_times[left]
            ) > window:
                right -= 1
            count = right - left
            neighbor_counts[left] = max(neighbor_counts[left], count)

        # --- 计算 burst_score ---
        # 将邻居数映射为 0.0~1.0 的分数
        # neighbor_count >= min_cluster_size-1 时开始有分
        # neighbor_count 越多分数越高
        temp_scores = [0.0] * m
        for i in range(m):
            nc = neighbor_counts[i]
            if nc >= self.min_cluster_size - 1:
                # 线性映射: min_cluster_size-1 → 0.3, 翻倍 → 1.0
                base = max(0, self.min_cluster_size - 1)
                ratio = min(1.0, (nc - base) / max(base, 1))
                temp_scores[i] = 0.3 + 0.7 * ratio

        # --- 映射回原始顺序 ---
        score_map = {sorted_indices[i]: temp_scores[i] for i in range(m)}
        burst_scores = [score_map.get(i, 0.0) for i in range(n)]

        # --- 识别并记录簇 ---
        clustered_count = sum(1 for s in burst_scores if s > 0)
        if clustered_count > 0:
            clusters = self._identify_clusters(
                sorted_indices, sorted_times, temp_scores, burst_scores
            )
            logger.info(
                f"[BurstDetector] 检测到 {len(clusters)} 个时间聚集簇, "
                f"{clustered_count}/{n} 条评论涉及时间聚集"
            )

        return burst_scores

    def _parse_time(self, time_str: str) -> datetime | None:
        """解析时间字符串，支持多种格式"""
        if not time_str or not isinstance(time_str, str):
            return None

        time_str = time_str.strip()
        if not time_str:
            return None

        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue

        return None

    def _identify_clusters(
        self,
        sorted_indices: list,
        sorted_times: list,
        temp_scores: list,
        burst_scores: list,
    ) -> list:
        """识别并标记独立的时间聚集簇"""
        clusters = []
        visited = set()
        window = timedelta(minutes=self.window_minutes)

        for i in range(len(sorted_indices)):
            if i in visited or temp_scores[i] <= 0:
                continue

            # 以当前评论为种子，扩展簇
            cluster_indices = [i]
            visited.add(i)
            center_time = sorted_times[i]

            for j in range(i + 1, len(sorted_indices)):
                if j in visited:
                    continue
                if abs(sorted_times[j] - center_time) <= window:
                    cluster_indices.append(j)
                    visited.add(j)

            if len(cluster_indices) >= self.min_cluster_size:
                cluster_times = [sorted_times[idx] for idx in cluster_indices]
                cluster_orig_indices = [sorted_indices[idx] for idx in cluster_indices]
                avg_score = sum(burst_scores[idx] for idx in cluster_orig_indices) / len(
                    cluster_orig_indices
                )
                clusters.append({
                    "start_time": min(cluster_times).strftime("%Y-%m-%d %H:%M:%S"),
                    "end_time": max(cluster_times).strftime("%Y-%m-%d %H:%M:%S"),
                    "review_count": len(cluster_indices),
                    "avg_burst_score": round(avg_score, 4),
                })

        return clusters

    def get_cluster_summary(self, reviews: list, burst_scores: list) -> dict:
        """
        生成聚集检测摘要

        Returns:
            dict with:
                - total_reviews: 总评论数
                - reviews_with_burst: 涉及聚集的评论数
                - burst_ratio: 聚集评论占比
                - clusters: 聚集簇列表
                - max_burst_score: 最高聚集分
                - avg_burst_score: 平均聚集分（仅算有分的）
        """
        n = len(reviews)
        burst_positive = [s for s in burst_scores if s > 0]
        clusters = self._find_clusters_for_summary(reviews, burst_scores)

        return {
            "total_reviews": n,
            "reviews_with_burst": len(burst_positive),
            "burst_ratio": round(len(burst_positive) / n, 4) if n > 0 else 0.0,
            "clusters": clusters,
            "max_burst_score": round(max(burst_scores), 4) if burst_scores else 0.0,
            "avg_burst_score": (
                round(sum(burst_positive) / len(burst_positive), 4)
                if burst_positive
                else 0.0
            ),
        }

    def _find_clusters_for_summary(
        self, reviews: list, burst_scores: list
    ) -> list:
        """为摘要提取聚集簇信息"""
        # 找出所有有 burst_score 的评论
        burst_reviews = [
            (i, reviews[i], burst_scores[i])
            for i in range(len(reviews))
            if burst_scores[i] > 0
        ]
        if not burst_reviews:
            return []

        # 按时间排序
        burst_reviews.sort(
            key=lambda x: x[1].get("comment_time", "")
        )

        # 简单分组：相邻评论时间在 window_minutes 内的归为同一簇
        clusters = []
        current_cluster = [burst_reviews[0]]
        window = timedelta(minutes=self.window_minutes)

        for i in range(1, len(burst_reviews)):
            prev_time = self._parse_time(
                burst_reviews[i - 1][1].get("comment_time", "")
            )
            curr_time = self._parse_time(
                burst_reviews[i][1].get("comment_time", "")
            )

            if prev_time and curr_time and (curr_time - prev_time) <= window:
                current_cluster.append(burst_reviews[i])
            else:
                if len(current_cluster) >= self.min_cluster_size:
                    clusters.append(self._build_cluster_info(current_cluster))
                current_cluster = [burst_reviews[i]]

        if len(current_cluster) >= self.min_cluster_size:
            clusters.append(self._build_cluster_info(current_cluster))

        return clusters

    def _build_cluster_info(self, cluster_items: list) -> dict:
        """构建单个簇的信息"""
        scores = [item[2] for item in cluster_items]
        times = [
            self._parse_time(item[1].get("comment_time", ""))
            for item in cluster_items
        ]
        valid_times = [t for t in times if t is not None]

        return {
            "review_count": len(cluster_items),
            "avg_burst_score": round(sum(scores) / len(scores), 4),
            "start_time": (
                min(valid_times).strftime("%Y-%m-%d %H:%M:%S")
                if valid_times
                else "未知"
            ),
            "end_time": (
                max(valid_times).strftime("%Y-%m-%d %H:%M:%S")
                if valid_times
                else "未知"
            ),
            "sample_texts": [
                item[1].get("text", "")[:30] for item in cluster_items[:3]
            ],
        }
