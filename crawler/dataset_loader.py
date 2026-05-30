"""
评论数据加载器 — 从本地CSV/JSON加载评论数据
替代京东爬虫，零网络依赖，零法律风险
"""
import logging
import random
import os
import pandas as pd

logger = logging.getLogger("ReviewGuard.DataLoader")

DATA_DIR = os.environ.get(
    "REVIEWGUARD_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
)


class DatasetLoader:
    """
    从本地数据集加载评论，接口完全兼容 JDCrawler。
    直接替换 Collector 的数据源，Agent 架构不变。
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.sample_size = self.config.get("sample_size", 20)
        self.data_source = self.config.get("data_source", "train_v2.csv")
        self._df_cache = None

    def _load_dataframe(self) -> pd.DataFrame:
        """加载CSV数据（首次调用时缓存）"""
        if self._df_cache is not None:
            return self._df_cache

        filepath = os.path.join(DATA_DIR, self.data_source)
        if not os.path.exists(filepath):
            logger.error(f"数据文件不存在: {filepath}")
            return pd.DataFrame()

        # CSV无表头
        df = pd.read_csv(filepath, header=None, names=[
            "label", "text", "comment_time", "extra_time", "username", "score"
        ])
        df["label"] = df["label"].astype(int)
        df["comment_time"] = df["comment_time"].astype(str).str.replace("/", "-", regex=False)
        df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(3).astype(int)
        self._df_cache = df
        logger.info(f"加载数据集: {len(df)} 条评论")
        return df

    def fetch_reviews(self, product_id: str = None, max_pages: int = 2) -> list:
        """
        从数据集中随机采样评论，模拟'采集到一批评论'。

        Args:
            product_id: 商品标识（用于报告，可选）
            max_pages: 模拟翻页（每页10条，默认取2页=20条）

        Returns:
            评论列表
        """
        df = self._load_dataframe()
        if df.empty:
            return []

        # 随机采样
        n = min(self.sample_size, len(df), max_pages * 10)
        sampled = df.sample(n=n, random_state=random.randint(0, 9999))

        reviews = []
        for _, row in sampled.iterrows():
            review = {
                "text": str(row.get("text", "")),
                "username": str(row.get("username", "")),
                "comment_time": str(row.get("comment_time", "")),
                "score": int(row.get("score", 3)),
                "product_id": product_id or "DATASET_SAMPLE",
            }
            reviews.append(review)

        logger.info(f"数据加载: {len(reviews)} 条评论 (product_id={product_id})")
        return reviews

    def fetch_product_info(self, product_id: str = None) -> dict:
        """获取商品信息（数据集场景下返回占位）"""
        df = self._load_dataframe()
        total = len(df) if not df.empty else 0
        return {
            "product_id": product_id or "DATASET_SAMPLE",
            "title": f"数据集评论样本 (共{total}条)",
        }
