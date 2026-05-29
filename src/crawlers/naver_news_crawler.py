from bs4 import BeautifulSoup
from src.crawlers.base_crawler import BaseCrawler
import urllib.parse
import re


class NaverNewsCrawler(BaseCrawler):
    """
    네이버 뉴스 검색 기반 크롤러.

    네이버 뉴스 검색 결과에서 'n.news.naver.com' 으로 시작하는 네이버 내 기사만 수집.
    (외부 언론사 리디렉션 URL은 제외 → 본문 추출 안정성 확보)

    검색 URL:
      https://search.naver.com/search.naver?where=news&query={Q}&start=N
    상세 URL 패턴:
      https://n.news.naver.com/mnews/article/{press_id}/{article_id}
    """

    def __init__(self, queries=None):
        super().__init__(source_name="Naver News")
        self.domain = "https://search.naver.com/search.naver"
        self.queries = queries or [
            "방송통신위원회",
            "공영방송",
            "언론노조",
            "방송 미디어 정책",
        ]

    def crawl(self, limit=10, progress_callback=None):
        self.logger.info(f"Starting crawl for {self.source_name}")
        results = []
        seen = set()
        per_query = max(1, limit // len(self.queries))

        for query in self.queries:
            query_count = 0
            # 네이버 검색은 한 페이지에 10개, start 파라미터로 페이지 이동
            for start in range(1, 401, 10):
                if len(results) >= limit or query_count >= per_query:
                    break

                params = {
                    "where": "news",
                    "query": query,
                    "start": str(start),
                    "sort": "1",  # 최신순
                }
                response = self.fetch_url(self.domain, params=params)
                if not response:
                    break

                soup = BeautifulSoup(response.text, "html.parser")

                # 네이버 뉴스 도메인 링크만 추출
                naver_links = []
                seen_ids_on_page = set()
                for a in soup.select('a[href*="n.news.naver.com"]'):
                    href = a.get("href", "")
                    m = re.search(
                        r"n\.news\.naver\.com/(?:mnews/)?article/(\d+)/(\d+)",
                        href,
                    )
                    if not m:
                        continue
                    press_id, article_id = m.group(1), m.group(2)
                    key = f"{press_id}_{article_id}"
                    if key in seen_ids_on_page:
                        continue
                    seen_ids_on_page.add(key)
                    naver_links.append((press_id, article_id, href))

                if not naver_links:
                    break

                page_added = 0
                for press_id, article_id, href in naver_links:
                    if len(results) >= limit or query_count >= per_query:
                        break

                    try:
                        # 정규화된 모바일 URL 사용 (구조 안정적)
                        detail_url = (
                            f"https://n.news.naver.com/mnews/article/"
                            f"{press_id}/{article_id}"
                        )
                        if detail_url in seen:
                            continue

                        detail_data = self.parse_detail(detail_url)
                        if not detail_data or not detail_data.get("title"):
                            continue

                        raw_date = detail_data.get("date")
                        if not self.is_recent_date(raw_date, allow_unknown=False):
                            continue

                        seen.add(detail_url)
                        query_count += 1
                        page_added += 1

                        unified = self.make_unified_data(
                            title=detail_data.get("title"),
                            date=raw_date,
                            content=detail_data.get("content"),
                            url=detail_url,
                            doc_id=f"naver_{press_id}_{article_id}",
                            company=detail_data.get("company"),
                            author=detail_data.get("author"),
                            summary=detail_data.get("summary"),
                            image_urls=detail_data.get("images", []),
                            references=[
                                {
                                    "query": query,
                                    "search_url": (
                                        f"{self.domain}?"
                                        f"{urllib.parse.urlencode(params)}"
                                    ),
                                }
                            ],
                        )
                        results.append(unified)

                        if progress_callback:
                            progress_callback(len(results), limit, query)
                        self.logger.info(
                            f"Successfully crawled: {detail_data.get('title')}"
                        )

                    except Exception as e:
                        self.logger.error(f"Error parsing Naver item: {e}")
                        continue

                if page_added == 0:
                    # 이 페이지에서 새로 수집된 것이 없으면 다음 쿼리로
                    break

        return results

    def parse_detail(self, url):
        response = self.fetch_url(url)
        if not response:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # 제목
        title = None
        title_elem = (
            soup.select_one("h2#title_area")
            or soup.select_one("h2.media_end_head_headline")
            or soup.select_one("h3#articleTitle")
        )
        if title_elem:
            title = title_elem.get_text(strip=True)
        else:
            og_title = soup.select_one('meta[property="og:title"]')
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()

        # 본문
        content_elem = (
            soup.select_one("article#dic_area")
            or soup.select_one("div#newsct_article")
            or soup.select_one("div#articleBodyContents")
        )

        # 날짜
        raw_date = None
        date_elem = soup.select_one(
            "span.media_end_head_info_datestamp_time"
        ) or soup.select_one("span._ARTICLE_DATE_TIME")
        if date_elem:
            raw_date = date_elem.get("data-date-time") or date_elem.get_text(strip=True)
        if not raw_date:
            meta_date = soup.select_one('meta[property="article:published_time"]')
            if meta_date and meta_date.get("content"):
                raw_date = meta_date["content"]

        # 언론사
        company = None
        press_elem = soup.select_one("a.media_end_head_top_logo img") or soup.select_one(
            ".media_end_head_top_logo img"
        )
        if press_elem and press_elem.get("title"):
            company = press_elem["title"].strip()
        if not company:
            meta_press = soup.select_one('meta[property="og:article:author"]')
            if meta_press and meta_press.get("content"):
                # 보통 "언론사 | 네이버 뉴스" 형식
                company = meta_press["content"].split("|")[0].strip()

        # 작성자
        author = None
        byline = soup.select_one("em.media_end_head_journalist_name") or soup.select_one(
            ".media_end_head_journalist_name"
        )
        if byline:
            author = byline.get_text(strip=True)

        # 요약
        summary = None
        meta_desc = soup.select_one('meta[property="og:description"]') or soup.select_one(
            'meta[name="description"]'
        )
        if meta_desc and meta_desc.get("content"):
            summary = meta_desc["content"].strip()

        images = []
        if content_elem:
            for img in content_elem.select("img"):
                src = (
                    img.get("data-src")
                    or img.get("src")
                    or img.get("data-original")
                )
                if src and not src.startswith("data:"):
                    images.append(urllib.parse.urljoin(url, src))

        return {
            "title": title,
            "date": raw_date,
            "content": content_elem,
            "company": company,
            "author": author,
            "summary": summary,
            "images": images,
        }
