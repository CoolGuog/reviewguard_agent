"""
ReviewGuard 主入口
电商虚假评论自治检测Agent系统
"""
import logging
import sys
import os

# 将项目根目录加入Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.bus import MessageBus
from core.orchestrator import Orchestrator
from core.llm_client import LLMClient
from core.storage import Storage
from agents.collector import CollectorAgent
from agents.detector import DetectorAgent
from agents.analyst import AnalystAgent
from agents.reporter import ReporterAgent
from models.detection_model import DetectionModel
from crawler.jd_crawler import JDCrawler
from crawler.dataset_loader import DatasetLoader
from config import (
    AGENT_CONFIG, CRAWLER_CONFIG, LLM_CONFIG,
    STORAGE_CONFIG, WEB_CONFIG, DATA_SOURCE,
)

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(STORAGE_CONFIG["log_dir"], "reviewguard.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("ReviewGuard")


def create_system() -> Orchestrator:
    """创建并初始化整个Agent系统"""
    orch = Orchestrator()

    # 1. 初始化存储
    storage = Storage(db_path=STORAGE_CONFIG["db_path"])

    # 2. 初始化LLM客户端（可选）
    llm_client = LLMClient(config=LLM_CONFIG) if LLM_CONFIG.get("api_key") else None

    # 3. 初始化检测模型
    model_config = AGENT_CONFIG.get("detector", {})
    detection_model = DetectionModel(
        model_path=model_config.get("model_path"),
        threshold=model_config.get("model_threshold"),  # None=使用checkpoint阈值
    )

    # 4. 初始化数据源
    if DATA_SOURCE == "selenium_csv":
        from crawler.selenium_crawler import DatasetLoader as SeleniumCSVLoader
        crawler = SeleniumCSVLoader(config=CRAWLER_CONFIG.get("selenium_csv", {}))
        logger.info("数据源: Selenium采集CSV")
    elif DATA_SOURCE == "dataset":
        crawler = DatasetLoader(config=CRAWLER_CONFIG.get("dataset", {}))
        logger.info("数据源: 本地数据集")
    else:
        crawler = JDCrawler(config=CRAWLER_CONFIG.get("jd", {}))
        logger.info("数据源: 京东爬虫")

    # 5. 创建各Agent
    collector = CollectorAgent(
        name="collector",
        bus=orch.bus,
        crawler=crawler,
        config=AGENT_CONFIG.get("collector", {}),
    )

    detector = DetectorAgent(
        name="detector",
        bus=orch.bus,
        model=detection_model,
        config=AGENT_CONFIG.get("detector", {}),
    )

    analyst = AnalystAgent(
        name="analyst",
        bus=orch.bus,
        config=AGENT_CONFIG.get("analyst", {}),
    )

    reporter = ReporterAgent(
        name="reporter",
        bus=orch.bus,
        llm_client=llm_client,
        config=AGENT_CONFIG.get("reporter", {}),
    )

    # 6. 注册Agent
    for agent in [collector, detector, analyst, reporter]:
        orch.add_agent(agent)

    # 7. 定义工作流
    orch.define_workflow([
        ("collector", "detector"),
        ("detector", "analyst"),
        ("analyst", "reporter"),
    ])

    logger.info(
        f"系统初始化完成, 已注册Agent: {orch.bus.get_agent_names()}"
    )

    # 把storage挂在orch上，方便Web层访问
    orch.storage = storage

    return orch


def run_once(product_ids: list = None):
    """执行一次完整流水线"""
    orch = create_system()
    orch.start_pipeline({"product_ids": product_ids or []})
    return orch


def run_periodic(interval_seconds: int = 1800, product_ids: list = None):
    """启动定时流水线"""
    orch = create_system()
    orch.start_periodic(interval_seconds, {"product_ids": product_ids or []})
    return orch


def run_web():
    """启动Web Dashboard"""
    orch = create_system()

    # 避免循环导入，延迟导入Web模块
    from web.app import create_app
    app = create_app(orch)
    app.run(
        host=WEB_CONFIG["host"],
        port=WEB_CONFIG["port"],
        debug=WEB_CONFIG["debug"],
    )


def parse_product_ids(raw_inputs: list) -> list:
    """
    从输入中提取商品ID。支持：
    - 纯数字ID: "100278983652"
    - 京东链接: "https://item.jd.com/100278983652.html"
    - 移动端链接: "https://item.m.jd.com/product/100278983652.html"
    - 数据集模式: 任意字符串均可（如 "SAMPLE", "phone" 等作为标识）
    """
    import re

    ids = []
    for raw in (raw_inputs or []):
        raw = raw.strip()
        if not raw:
            continue
        # 尝试从链接中提取
        match = re.search(r"(?:item\.(?:m\.)?jd\.com/(?:product/)?)(\d+)", raw)
        if match:
            ids.append(match.group(1))
        elif raw.isdigit():
            ids.append(raw)
        else:
            # 数据集模式：非数字非链接的输入直接作为标识
            ids.append(raw)
    return ids


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ReviewGuard - 电商虚假评论自治检测Agent系统")
    parser.add_argument("command", choices=["once", "periodic", "web"], help="运行模式")
    parser.add_argument("--products", nargs="+", help="商品ID或链接（支持纯数字和多格式链接）")
    parser.add_argument("--interval", type=int, default=1800, help="定时模式间隔（秒）")

    args = parser.parse_args()

    product_ids = parse_product_ids(args.products)

    if args.command == "once":
        logger.info("=== 单次执行模式 ===")
        orch = run_once(product_ids=product_ids)
        print(f"执行完成, 系统状态: {orch.get_status()}")

    elif args.command == "periodic":
        logger.info(f"=== 定时执行模式, 间隔{args.interval}秒 ===")
        orch = run_periodic(
            interval_seconds=args.interval,
            product_ids=product_ids,
        )
        try:
            # 保持主线程运行
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            orch.stop_periodic()
            print("\n已停止")

    elif args.command == "web":
        logger.info("=== Web Dashboard模式 ===")
        run_web()
