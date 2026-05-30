"""
京东评论爬虫 — 需浏览器Cookie鉴权。

获取Cookie：浏览器打开 item.jd.com → F12 → Network → 复制Cookie
粘贴到 config.py 的 CRAWLER_CONFIG.jd.cookie_string 中。

注意：国内电商评论API普遍受限，默认推荐使用 dataset_loader.py。
"""
import logging
import random
import time
import json
import re
import requests

logger = logging.getLogger("ReviewGuard.JDCrawler")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]


class JDCrawler:
    """京东商品评论爬虫"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.page_size = self.config.get("page_size", 10)
        self.delay_range = self.config.get("delay_range", (2, 5))
        self.max_retries = self.config.get("max_retries", 3)
        self._cookie_string = self.config.get("cookie_string", "")

    def _create_session(self) -> requests.Session:
        s = requests.Session()
        if self._cookie_string:
            for item in self._cookie_string.split(";"):
                item = item.strip()
                if "=" in item:
                    k, v = item.split("=", 1)
                    s.cookies.set(k, v)

        s.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        })
        return s

    def fetch_reviews(self, product_id: str, max_pages: int = 2) -> list:
        """爬取评论"""
        session = self._create_session()

        # 访问商品页建立会话
        try:
            r = session.get(f"https://item.jd.com/{product_id}.html", timeout=15)
            logger.debug(f"预热商品页: {r.status_code}")
        except Exception:
            pass
        time.sleep(random.uniform(1, 2))

        all_reviews = []
        for page in range(max_pages):
            try:
                reviews = self._fetch_page(session, product_id, page)
                if not reviews:
                    break
                all_reviews.extend(reviews)
                logger.info(f"商品{product_id}第{page}页获取{len(reviews)}条，累计{len(all_reviews)}条")
                time.sleep(random.uniform(*self.delay_range))
            except Exception as e:
                logger.error(f"请求失败 (商品={product_id}, 页={page}): {e}")
                break

        session.close()
        return all_reviews

    def _fetch_page(self, session, product_id: str, page: int) -> list:
        url = "https://club.jd.com/comment/productPageComments.action"
        params = {
            "callback": "fetchJSON_comment98",
            "productId": product_id,
            "score": 0,
            "sortType": 5,
            "page": page,
            "pageSize": self.page_size,
            "isShadowSku": 0,
            "folded": 0,
        }
        session.headers["Referer"] = f"https://item.jd.com/{product_id}.html"

        for attempt in range(self.max_retries):
            try:
                resp = session.get(url, params=params, timeout=15)
                text = resp.text.strip()
                if text == "系统繁忙":
                    logger.debug("京东API: 系统繁忙")
                    time.sleep(random.uniform(3, 8) * (attempt + 1))
                    continue
                if "fetchJSON_comment98" in text:
                    text = text[text.find("{"):text.rfind("}") + 1]
                if "{" in text:
                    data = json.loads(text)
                    return self._parse_comments(data, product_id)
                return []
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(random.uniform(1, 3) * (2 ** attempt))
                else:
                    raise e
        return []

    def _parse_comments(self, data: dict, product_id: str) -> list:
        comments = data.get("comments", [])
        reviews = []
        for item in comments:
            text = (item.get("content") or "").strip()
            if not text:
                continue
            reviews.append({
                "text": text,
                "username": item.get("nickname", ""),
                "comment_time": item.get("creationTime", ""),
                "score": item.get("score", 0),
                "product_id": product_id,
            })
        return reviews

    def fetch_product_info(self, product_id: str) -> dict:
        try:
            session = self._create_session()
            r = session.get(f"https://item.jd.com/{product_id}.html", timeout=15)
            title = ""
            m = re.search(r"<title>(.+?)</title>", r.text)
            if m:
                title = re.sub(r"【.*?】", "", m.group(1)).strip()
            session.close()
            return {"product_id": product_id, "title": title or f"商品{product_id}"}
        except Exception:
            return {"product_id": product_id, "title": f"商品{product_id}"}