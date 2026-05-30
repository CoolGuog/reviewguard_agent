"""
ReviewGuard - 电商虚假评论自治检测Agent系统
配置文件
"""

# ==================== 数据源 ====================
# "dataset":     从训练集CSV随机采样（演示用）
# "selenium_csv": 从Selenium定时采集的CSV加载（推荐）
# "jd_crawler":   直接请求京东API（需要有效Cookie，容易被封）
DATA_SOURCE = "dataset"

# ==================== Agent 配置 ====================
AGENT_CONFIG = {
    "collector": {
        "interval_seconds": 1800,   # 采集间隔（秒）
        "max_pages": 2,             # 每次采集最大页数（1页=10条，2页够用）
        "delay_range": (3, 6),      # 爬虫请求随机延时（秒），模拟人工浏览
        # Selenium定时采集监控的商品ID列表
        "product_ids": [],          # 如: ["100009077474", "10177285912219"]
    },
    "detector": {
        "model_path": "best_geo_poison_detector_v3.pt",
        "batch_size": 32,
        "confidence_threshold": 0.6, # 低于此置信度标记"待人工审核"
        "model_threshold": 0.985,    # 模型判定阈值：越高假评越少（更严格）
                                      # 0.985: 攻击全检出 + 70%正常判对（T=1.0下）
        # Burst 时间聚类检测配置
        "burst_window_minutes": 30,   # 时间窗口（分钟），窗口内评论数>=阈值视为聚集
        "burst_min_cluster_size": 3,  # 最小聚集数
        "burst_weight": 0.25,         # burst分与模型分的融合权重
    },
    "analyst": {
        "time_window_minutes": 60,   # 攻击事件识别的时间窗口
        "min_fake_count": 5,         # 时间窗口内假评数>=此值视为攻击事件
        "fake_ratio_threshold": 0.5, # 时间窗口内假评比例>=此值视为攻击事件
    },
    "reporter": {
        "output_dir": "data/reports",
        "alert_levels": {            # 告警分级
            "critical": 0.8,         # 假评比例>=80%
            "warning": 0.5,          # 假评比例>=50%
            "info": 0.0,             # 其他
        },
    },
}

# ==================== 爬虫配置 ====================
CRAWLER_CONFIG = {
    # 数据集加载器配置
    "dataset": {
        "data_source": "train_v2.csv",
        "sample_size": 20,
    },
    # Selenium采集CSV配置
    "selenium_csv": {
        "csv_file": "data/crawled/jd_reviews.csv",
        "sample_size": 20,
    },
    # 京东爬虫配置
    "jd": {
        "base_url": "https://club.jd.com/comment/productPageComments.action",
        # 浏览器Cookie: F12 → Application → Cookies → 全选复制 → 粘贴到下面
        "cookie_string": "",  # 留空，使用时填入你的京东Cookie,
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": "https://item.jd.com/",
        },
        "page_size": 10,          # 每页评论数（上限10）
        "max_retries": 3,         # API请求重试次数
    },
}

# ==================== LLM 配置 ====================
LLM_CONFIG = {
    "provider": "openai",       # openai / deepseek
    "api_key": "",              # 填你的API Key
    "base_url": "",             # DeepSeek: https://api.deepseek.com
    "model": "gpt-3.5-turbo",  # 或 deepseek-chat
    "max_tokens": 1024,
    "temperature": 0.3,
}

# ==================== 存储配置 ====================
STORAGE_CONFIG = {
    "db_path": "data/reviewguard.db",
    "log_dir": "logs",
}

# ==================== Web 配置 ====================
WEB_CONFIG = {
    "host": "0.0.0.0",
    "port": 5000,
    "debug": True,
}
