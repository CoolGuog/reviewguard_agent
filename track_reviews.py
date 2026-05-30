"""
ReviewGuard — 京东评论持续跟踪采集工具

输入京东商品链接（或纯数字ID），定时爬取评论，增量写入 CSV，
可选每次采集后自动触发 ReviewGuard 检测流水线。

用法:
  # 单次采集
  python track_reviews.py once --products https://item.jd.com/100009077474.html

  # 单次采集 + 自动检测
  python track_reviews.py once --products https://item.jd.com/100009077474.html --detect

  # 持续跟踪（每小时一次）
  python track_reviews.py track --products https://item.jd.com/100009077474.html --interval 3600

  # 持续跟踪 + 每次采集后自动检测
  python track_reviews.py track --products https://item.jd.com/100009077474.html --interval 3600 --detect

  # 多个商品
  python track_reviews.py once --products https://item.jd.com/100009077474.html 10177285912219

CSV 输出路径: data/crawled/jd_reviews.csv
"""
import sys
import os
import time
import signal
import logging
import argparse

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler.selenium_crawler import SeleniumJDCrawler
from config import STORAGE_CONFIG, CRAWLER_CONFIG

# ── 全局 shutdown 标志（用于 track 模式优雅退出） ──
_shutdown_flag = False


def _signal_handler(signum, frame):
    global _shutdown_flag
    logger = logging.getLogger("ReviewGuard.Tracker")
    logger.info("收到停止信号，正在安全关闭...")
    _shutdown_flag = True


def setup_logging():
    """配置日志：同时输出到控制台和文件"""
    os.makedirs(STORAGE_CONFIG.get("log_dir", "logs"), exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                os.path.join(STORAGE_CONFIG["log_dir"], "track_reviews.log"),
                encoding="utf-8",
            ),
        ],
    )
    return logging.getLogger("ReviewGuard.Tracker")


def crawl_single_product(crawler, product_id: str, max_scrolls: int, max_pages: int) -> int:
    """
    爬取单个商品的评论并写入 CSV。

    Returns:
        本次新增的评论数
    """
    logger = logging.getLogger("ReviewGuard.Tracker")
    try:
        reviews = crawler.fetch_reviews(
            product_id, max_scrolls=max_scrolls, max_pages=max_pages
        )
        if reviews:
            new_count = crawler.save_to_csv(reviews)
            logger.info(f"  商品 {product_id}: 获取 {len(reviews)} 条, 新增 {new_count} 条")
            return new_count
        else:
            logger.warning(f"  商品 {product_id}: 未获取到评论")
            return 0
    except Exception as e:
        logger.error(f"  商品 {product_id} 爬取失败: {e}", exc_info=True)
        return 0


def run_crawl_round(crawler, product_ids: list, max_scrolls: int, max_pages: int) -> int:
    """
    执行一轮完整采集：遍历所有商品，逐个爬取。

    Returns:
        本轮总新增评论数
    """
    logger = logging.getLogger("ReviewGuard.Tracker")
    total_new = 0

    for pid in product_ids:
        new_count = crawl_single_product(crawler, pid, max_scrolls, max_pages)
        total_new += new_count
        # 商品间间隔，降低反爬风险
        if pid != product_ids[-1]:
            time.sleep(5)

    return total_new


def run_detection(product_ids: list):
    """
    触发 ReviewGuard 检测流水线。
    要求 config.py 中 DATA_SOURCE = "selenium_csv"。
    """
    from main import create_system  # 延迟导入，避免 --help 触发 ML 依赖
    from config import DATA_SOURCE

    logger = logging.getLogger("ReviewGuard.Tracker")

    if DATA_SOURCE != "selenium_csv":
        logger.warning(
            f"当前 DATA_SOURCE = '{DATA_SOURCE}'，检测流水线将不会读取爬取到的 CSV。"
            f"请在 config.py 中设置 DATA_SOURCE = 'selenium_csv' 后重试。"
        )
        return

    try:
        logger.info("启动 ReviewGuard 检测流水线...")
        orch = create_system()
        orch.start_pipeline({"product_ids": product_ids})
        status = orch.get_status()
        logger.info(f"检测流水线完成, 系统状态: {status}")
    except Exception as e:
        logger.error(f"检测流水线执行失败: {e}", exc_info=True)


def run_once(args):
    """单次采集模式"""
    logger = setup_logging()
    product_ids = _resolve_product_ids(args)

    logger.info("=" * 60)
    logger.info(f"单次采集模式 | 商品: {product_ids}")
    logger.info(f"翻页数: {args.max_pages}, 滚动深度: {args.max_scrolls}")
    logger.info("=" * 60)

    cookie = CRAWLER_CONFIG.get("jd", {}).get("cookie_string", "")
    crawler = SeleniumJDCrawler(output_dir="data/crawled", cookie_string=cookie)

    try:
        total_new = run_crawl_round(
            crawler, product_ids, args.max_scrolls, args.max_pages
        )
        logger.info(f"采集完成, 本轮新增 {total_new} 条评论")

        if args.detect:
            logger.info("---")
            run_detection(product_ids)

    finally:
        crawler.close()
        logger.info("浏览器已关闭")

    _print_summary(logger, product_ids, total_new)


def run_track(args):
    """持续跟踪模式"""
    logger = setup_logging()
    product_ids = _resolve_product_ids(args)

    # 注册信号处理（SIGTERM 在 Windows 上不可注册，仅捕获 SIGINT/Ctrl+C）
    signal.signal(signal.SIGINT, _signal_handler)
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, AttributeError):
        pass  # Windows 不支持 SIGTERM handler

    logger.info("=" * 60)
    logger.info(f"持续跟踪模式 | 商品: {product_ids}")
    logger.info(f"间隔: {args.interval}秒 | 翻页: {args.max_pages} | 滚动: {args.max_scrolls}")
    logger.info(f"自动检测: {'是' if args.detect else '否'}")
    logger.info("按 Ctrl+C 停止")
    logger.info("=" * 60)

    cookie = CRAWLER_CONFIG.get("jd", {}).get("cookie_string", "")
    crawler = SeleniumJDCrawler(output_dir="data/crawled", cookie_string=cookie)
    round_num = 0

    try:
        while not _shutdown_flag:
            round_num += 1
            round_start = time.time()

            logger.info(f"\n{'─' * 40}")
            logger.info(f"第 {round_num} 轮采集开始")
            logger.info(f"{'─' * 40}")

            try:
                total_new = run_crawl_round(
                    crawler, product_ids, args.max_scrolls, args.max_pages
                )
                logger.info(f"第 {round_num} 轮完成, 新增 {total_new} 条评论")

                if args.detect and total_new > 0:
                    run_detection(product_ids)
                elif args.detect:
                    logger.info("本轮无新增评论，跳过检测")

            except Exception as e:
                logger.error(f"第 {round_num} 轮异常: {e}", exc_info=True)

            # 计算下一轮等待时间
            elapsed = time.time() - round_start
            wait_seconds = max(0, args.interval - elapsed)
            logger.info(
                f"本轮耗时 {elapsed:.0f}秒, "
                f"等待 {wait_seconds:.0f}秒 后开始下一轮..."
            )

            # 分片等待，以便及时响应 shutdown 信号
            while wait_seconds > 0 and not _shutdown_flag:
                time.sleep(min(1, wait_seconds))
                wait_seconds -= 1

    finally:
        logger.info("正在关闭浏览器...")
        crawler.close()
        logger.info(f"已停止。共执行 {round_num} 轮采集。")


def _resolve_product_ids(args) -> list:
    """解析命令行输入的链接/ID，返回纯商品ID列表"""
    from main import parse_product_ids  # 延迟导入，避免 --help 触发 ML 依赖

    logger = logging.getLogger("ReviewGuard.Tracker")
    product_ids = parse_product_ids(args.products)
    if not product_ids:
        logger.error("未能从输入中提取到有效商品ID。请提供京东链接或纯数字ID。")
        logger.error("示例: python track_reviews.py once --products https://item.jd.com/100009077474.html")
        sys.exit(1)
    return product_ids


def _print_summary(logger, product_ids: list, total_new: int):
    """打印采集摘要"""
    csv_path = os.path.join("data", "crawled", "jd_reviews.csv")
    logger.info(f"\n{'=' * 60}")
    logger.info("采集摘要")
    logger.info(f"{'=' * 60}")
    logger.info(f"  商品数: {len(product_ids)}")
    logger.info(f"  新增评论: {total_new} 条")
    logger.info(f"  CSV 文件: {csv_path}")
    if os.path.exists(csv_path):
        import pandas as pd
        df = pd.read_csv(csv_path)
        logger.info(f"  CSV 总评论数: {len(df)} 条")
    logger.info(f"{'=' * 60}")


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ReviewGuard — 京东评论持续跟踪采集工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python track_reviews.py once --products https://item.jd.com/100009077474.html
  python track_reviews.py track --products https://item.jd.com/100009077474.html --interval 3600 --detect
  python track_reviews.py once --products 100009077474 10177285912219 --detect
        """,
    )
    parser.add_argument(
        "command", choices=["once", "track"],
        help="运行模式: once=单次采集, track=持续跟踪"
    )
    parser.add_argument(
        "--products", nargs="+", required=True,
        help="商品ID或京东链接（支持多个，空格分隔）"
    )
    parser.add_argument(
        "--interval", type=int, default=3600,
        help="跟踪模式下的采集间隔（秒），默认 3600（1小时）"
    )
    parser.add_argument(
        "--detect", action="store_true",
        help="每次采集后自动运行 ReviewGuard 检测流水线"
    )
    parser.add_argument(
        "--max-scrolls", type=int, default=5,
        help="每页最大滚动次数（默认 5）"
    )
    parser.add_argument(
        "--max-pages", type=int, default=1,
        help="最大翻页数（默认 1，设为 3 可获取更多历史评论）"
    )

    args = parser.parse_args()

    if args.command == "once":
        run_once(args)
    elif args.command == "track":
        run_track(args)
