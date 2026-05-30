"""
Selenium 真浏览器爬虫 — 模拟真人操作，不易被反爬检测。
用于定时采集京东商品评论，增量存入CSV。
"""
import logging
import time
import random
import os
import hashlib
import pandas as pd
from datetime import datetime

logger = logging.getLogger("ReviewGuard.SeleniumCrawler")


class SeleniumJDCrawler:
    """用真实Chrome浏览器爬取京东评论"""

    def __init__(self, output_dir: str = "data/crawled", cookie_string: str = ""):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self._driver = None
        self._cookie_string = cookie_string  # JD登录Cookie，用于绕过登录墙
        self._cookies_injected = False       # 是否已注入Cookie

    # 常见浏览器路径（Windows），按优先级排列
    _BROWSER_CANDIDATES = [
        # Google Chrome
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        # Microsoft Edge (Chromium 内核)
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]

    @classmethod
    def _find_browser(cls) -> dict | None:
        """
        自动查找可用的 Chromium 内核浏览器。
        Returns: {"path": str, "type": "chrome"|"edge"} 或 None
        """
        # 优先使用环境变量
        for env_var in ("CHROME_BINARY_PATH", "EDGE_BINARY_PATH", "BROWSER_BINARY_PATH"):
            path = os.environ.get(env_var)
            if path and os.path.isfile(path):
                btype = "chrome" if "chrome" in path.lower() else "edge"
                logger.info(f"使用环境变量指定的浏览器: {path}")
                return {"path": path, "type": btype}

        # 自动检测
        for candidate in cls._BROWSER_CANDIDATES:
            if os.path.isfile(candidate):
                btype = "chrome" if "chrome" in candidate.lower() else "edge"
                logger.info(f"自动检测到浏览器: {candidate} ({btype})")
                return {"path": candidate, "type": btype}

        return None

    def _get_driver(self):
        """
        初始化浏览器驱动。
        Chrome → undetected_chromedriver（反爬能力强）
        Edge   → Selenium 原生驱动 + 反检测脚本
        """
        browser = self._find_browser()
        if not browser:
            raise RuntimeError(
                "未找到可用的浏览器。请安装 Google Chrome 或 Microsoft Edge 后重试。\n"
                "或设置环境变量 CHROME_BINARY_PATH / EDGE_BINARY_PATH 指定浏览器路径。\n"
                "Chrome 下载: https://www.google.com/chrome/\n"
                "Edge 下载: https://www.microsoft.com/edge/"
            )

        options = None

        if browser["type"] == "chrome":
            # Chrome: 使用 undetected_chromedriver，反爬效果最好
            import undetected_chromedriver as uc
            options = uc.ChromeOptions()
            options.binary_location = browser["path"]
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--window-size=1920,1080")
            driver = uc.Chrome(options=options)

        else:
            # Edge: 标准 Selenium + 手动反检测注入 + Selenium Manager 下载 msedgedriver
            from selenium import webdriver
            from selenium.webdriver.edge.options import Options as EdgeOptions

            options = EdgeOptions()
            options.binary_location = browser["path"]
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            driver = webdriver.Edge(options=options)

            # 手动注入反检测脚本（selenium-stealth 的 Edge 等价实现）
            self._apply_stealth_js(driver)

        return driver

    @staticmethod
    def _apply_stealth_js(driver):
        """
        通过 CDP (Page.addScriptToEvaluateOnNewDocument) 在页面加载前注入反检测脚本。
        这比 execute_script 更早执行，能阻止 navigator.webdriver 等属性被设置。
        兼容 Chrome 和 Edge。
        """
        stealth_js = """
        // 在页面 JS 执行前覆盖 webdriver 检测
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

        // 伪造 plugins 数组
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const plugins = [
                    {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
                    {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''},
                    {name: 'Native Client', filename: 'internal-nacl-plugin', description: ''},
                ];
                plugins.item = (i) => plugins[i];
                plugins.namedItem = (name) => plugins.find(p => p.name === name) || null;
                plugins.refresh = () => {};
                return plugins;
            }
        });

        // 伪造 languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en']
        });

        // 伪造 chrome.runtime
        window.chrome = {runtime: {}, app: {}};

        // 伪造 permissions.query
        const _query = window.navigator.permissions.query.bind(window.navigator.permissions);
        window.navigator.permissions.query = (params) => (
            params.name === 'notifications' ?
            Promise.resolve({state: Notification.permission}) :
            _query(params)
        );
        """
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": stealth_js
        })

    def _inject_cookies(self, driver):
        """将 Cookie 字符串注入浏览器，绕过京东登录墙"""
        logger.info("注入京东登录 Cookie...")
        # 必须先访问京东域名才能设置 Cookie
        driver.get("https://www.jd.com/")
        time.sleep(2)

        for item in self._cookie_string.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                key = key.strip()
                value = value.strip()
                try:
                    driver.add_cookie({"name": key, "value": value, "domain": ".jd.com"})
                except Exception:
                    pass  # 某些 Cookie 可能因 domain 不匹配而失败，忽略

        logger.info("Cookie 注入完成")

    def fetch_reviews(self, product_id: str, max_scrolls: int = 5, max_pages: int = 1) -> list:
        """
        打开商品页，滚动加载评论，提取数据。

        Args:
            product_id: 京东商品ID
            max_scrolls: 每页最大滚动次数（每次滚动可能加载一批评论）
            max_pages: 最大翻页数（默认1页，保持向后兼容）

        Returns:
            评论列表
        """
        if self._driver is None:
            self._driver = self._get_driver()

        driver = self._driver
        url = f"https://item.jd.com/{product_id}.html"
        all_reviews = []

        try:
            # 首次访问时注入 Cookie（需要登录才能看评论）
            if self._cookie_string and not self._cookies_injected:
                self._inject_cookies(driver)
                self._cookies_injected = True

            logger.info(f"打开商品页: {product_id}")
            driver.get(url)
            time.sleep(random.uniform(3, 5))
        except Exception as e:
            logger.error(f"打开商品页失败: {e}")
            return []

        for page in range(max_pages):
            try:
                # 滚动加载当前页评论
                for i in range(max_scrolls):
                    driver.execute_script(
                        f"window.scrollTo(0, document.body.scrollHeight * {0.3 + i * 0.15})"
                    )
                    time.sleep(random.uniform(1.5, 3))

                # 尝试点击"商品评价"标签（仅第一页需要）
                if page == 0:
                    try:
                        from selenium.webdriver.common.by import By
                        tab = driver.find_element(
                            By.XPATH, '//li[contains(text(), "商品评价") or contains(text(), "评价")]'
                        )
                        tab.click()
                        time.sleep(2)
                    except Exception:
                        pass

                # 提取当前页评论
                page_reviews = self._extract_page_reviews(driver, product_id)
                all_reviews.extend(page_reviews)
                logger.info(
                    f"商品 {product_id} 第{page+1}页: 提取到 {len(page_reviews)} 条评论, "
                    f"累计 {len(all_reviews)} 条"
                )

                if page < max_pages - 1:
                    # 尝试点击"下一页"
                    try:
                        from selenium.webdriver.common.by import By
                        next_btn = driver.find_element(
                            By.XPATH,
                            '//a[contains(text(), "下一页") or contains(@class, "next") '
                            'or contains(@class, "btn-next")]'
                        )
                        if next_btn.is_enabled():
                            next_btn.click()
                            time.sleep(random.uniform(2, 4))
                        else:
                            logger.info(f"已到最后一页（按钮已禁用）")
                            break
                    except Exception:
                        logger.info(f"未找到下一页按钮，评论已全部获取")
                        break

            except Exception as e:
                logger.error(f"商品 {product_id} 第{page+1}页提取失败: {e}")
                break

        return all_reviews

    def _extract_page_reviews(self, driver, product_id: str) -> list:
        """提取当前页面可见的评论列表"""
        from selenium.webdriver.common.by import By

        reviews = []
        try:
            comment_els = driver.find_elements(By.CSS_SELECTOR, '.comment-item')
            if not comment_els:
                comment_els = driver.find_elements(By.CSS_SELECTOR, '[class*="comment"]')

            for el in comment_els[:50]:  # 每页最多50条
                # --- 提取评论文本 ---
                try:
                    text_el = el.find_element(By.CSS_SELECTOR, '[class*="content"], .comment-con')
                    text = text_el.text.strip()
                except Exception:
                    text = el.text.strip()

                if not text or len(text) < 2:
                    continue

                # --- 提取用户名 ---
                try:
                    user_el = el.find_element(By.CSS_SELECTOR, '[class*="user"], [class*="nick"]')
                    username = user_el.text.strip()
                except Exception:
                    username = ""

                # --- 提取评分 ---
                try:
                    star_el = el.find_element(By.CSS_SELECTOR, '[class*="star"]')
                    star_text = star_el.get_attribute("class") or ""
                    import re
                    m = re.search(r'star(\d)', star_text)
                    score = int(m.group(1)) if m else 0
                except Exception:
                    score = 0

                # --- 提取真实评论发布时间（优先），失败则用当前时间 ---
                try:
                    date_el = el.find_element(
                        By.CSS_SELECTOR,
                        '[class*="time"], [class*="date"], [class*="creation"]'
                    )
                    comment_time = date_el.text.strip()
                    # 如果提取到的不是有效时间格式，回退
                    if not comment_time or len(comment_time) < 8:
                        comment_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    comment_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 生成唯一ID用于去重
                review_id = hashlib.md5(text.encode()).hexdigest()[:12]

                reviews.append({
                    "review_id": review_id,
                    "product_id": str(product_id),
                    "text": text,
                    "username": username,
                    "score": score,
                    "comment_time": comment_time,
                    "crawled_at": datetime.now().isoformat(),
                })

        except Exception as e:
            logger.error(f"提取评论失败: {e}")

        return reviews

    def save_to_csv(self, reviews: list, filename: str = "jd_reviews.csv"):
        """增量写入CSV，按 review_id 去重"""
        filepath = os.path.join(self.output_dir, filename)

        new_df = pd.DataFrame(reviews)
        if new_df.empty:
            return 0

        if os.path.exists(filepath):
            existing = pd.read_csv(filepath)
            # 去重
            existing_ids = set(existing["review_id"].tolist()) if "review_id" in existing.columns else set()
            new_df = new_df[~new_df["review_id"].isin(existing_ids)]
            if new_df.empty:
                logger.info("无新评论（全部已存在）")
                return 0
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df

        combined.to_csv(filepath, index=False, encoding="utf-8-sig")
        logger.info(f"保存 {len(new_df)} 条新评论，CSV共 {len(combined)} 条")
        return len(new_df)

    def close(self):
        if self._driver:
            self._driver.quit()
            self._driver = None


class DatasetLoader:
    """
    从 Selenium 爬取的 CSV 加载评论。
    接口与 dataset_loader.DatasetLoader 完全一致，可无缝替换 Collector 的数据源。
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.csv_file = self.config.get("csv_file", "data/crawled/jd_reviews.csv")
        self.sample_size = self.config.get("sample_size", 20)

    def fetch_reviews(self, product_id: str = None, max_pages: int = None) -> list:
        csv_path = self.csv_file
        if not os.path.exists(csv_path):
            logger.warning(f"CSV不存在: {csv_path}")
            return []

        df = pd.read_csv(csv_path)

        # 如果指定了商品ID，只取该商品的评论
        if product_id and "product_id" in df.columns:
            df = df[df["product_id"] == str(product_id)]

        if df.empty:
            return []

        # 取最新的 N 条
        n = min(self.sample_size, len(df))
        df = df.tail(n)

        reviews = []
        for _, row in df.iterrows():
            reviews.append({
                "text": str(row.get("text", "")),
                "username": str(row.get("username", "")),
                "comment_time": str(row.get("comment_time", "")),
                "score": int(row.get("score", 0) if not pd.isna(row.get("score", 0)) else 0),
                "product_id": str(row.get("product_id", product_id or "")),
            })

        logger.info(f"CSV加载: {len(reviews)} 条评论 (product_id={product_id})")
        return reviews

    def fetch_product_info(self, product_id: str = None) -> dict:
        return {"product_id": product_id or "", "title": f"爬取数据 ({self.csv_file})"}
