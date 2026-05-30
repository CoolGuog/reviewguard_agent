"""
数据存储层 — SQLite存储评论、检测结果和攻击事件
"""
import logging
import sqlite3
import os
import json
from datetime import datetime

logger = logging.getLogger("ReviewGuard.Storage")


class Storage:
    """SQLite存储层"""

    def __init__(self, db_path: str = "data/reviewguard.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = None
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        cursor = self._conn.cursor()

        # 评论表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                text TEXT NOT NULL,
                user TEXT DEFAULT '',
                time TEXT DEFAULT '',
                score INTEGER DEFAULT 0,
                label TEXT DEFAULT '',
                confidence REAL DEFAULT 0.0,
                review_status TEXT DEFAULT '',
                crawl_batch_id INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 攻击事件表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS attack_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                product_id TEXT NOT NULL,
                total_reviews INTEGER DEFAULT 0,
                fake_count INTEGER DEFAULT 0,
                fake_ratio REAL DEFAULT 0.0,
                severity TEXT DEFAULT 'info',
                attack_type TEXT DEFAULT '',
                profile TEXT DEFAULT '{}',
                detected_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 检测统计表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS detection_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                total INTEGER DEFAULT 0,
                fake INTEGER DEFAULT 0,
                real INTEGER DEFAULT 0,
                avg_confidence REAL DEFAULT 0.0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._conn.commit()
        logger.info(f"数据库初始化完成: {self.db_path}")

    def save_reviews(self, reviews: list, batch_id: int = 0):
        """保存评论检测结果"""
        cursor = self._conn.cursor()
        for r in reviews:
            cursor.execute("""
                INSERT INTO reviews (product_id, text, user, time, score, label, confidence, review_status, crawl_batch_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r.get("product_id", ""),
                r.get("text", ""),
                r.get("user", ""),
                r.get("time", ""),
                r.get("score", 0),
                r.get("label", ""),
                r.get("confidence", 0.0),
                r.get("review_status", ""),
                batch_id,
            ))
        self._conn.commit()

    def save_attack_event(self, event: dict):
        """保存攻击事件"""
        cursor = self._conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO attack_events
                (event_id, product_id, total_reviews, fake_count, fake_ratio, severity, attack_type, profile, detected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.get("event_id", ""),
                event.get("product_id", ""),
                event.get("total_reviews", 0),
                event.get("fake_count", 0),
                event.get("fake_ratio", 0.0),
                event.get("severity", "info"),
                event.get("profile", {}).get("attack_type", ""),
                json.dumps(event.get("profile", {}), ensure_ascii=False),
                event.get("detection_time", datetime.now().isoformat()),
            ))
            self._conn.commit()
        except sqlite3.IntegrityError:
            logger.debug(f"攻击事件已存在: {event.get('event_id')}")

    def save_detection_stats(self, batch_id: int, results: list):
        """保存批次检测统计"""
        cursor = self._conn.cursor()
        total = len(results)
        fake = sum(1 for r in results if r.get("label") == "fake")
        confs = [r.get("confidence", 0) for r in results]
        avg_conf = sum(confs) / len(confs) if confs else 0

        cursor.execute("""
            INSERT INTO detection_stats (batch_id, total, fake, real, avg_confidence)
            VALUES (?, ?, ?, ?, ?)
        """, (batch_id, total, fake, total - fake, round(avg_conf, 4)))
        self._conn.commit()

    def get_recent_reviews(self, limit: int = 50) -> list:
        """获取最近的评论"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM reviews ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_attack_events(self, limit: int = 20) -> list:
        """获取攻击事件"""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM attack_events ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_stats(self) -> dict:
        """获取全局统计"""
        cursor = self._conn.cursor()

        cursor.execute("SELECT COUNT(*) as total, SUM(CASE WHEN label='fake' THEN 1 ELSE 0 END) as fake FROM reviews")
        row = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as cnt FROM attack_events")
        events_count = cursor.fetchone()["cnt"]

        return {
            "total_reviews": row["total"],
            "fake_reviews": row["fake"] or 0,
            "real_reviews": (row["total"] or 0) - (row["fake"] or 0),
            "attack_events": events_count,
        }

    def close(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            logger.info("数据库连接已关闭")
