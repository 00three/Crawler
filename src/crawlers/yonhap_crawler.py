from bs4 import BeautifulSoup
from src.crawlers.base_crawler import BaseCrawler
import urllib.parse
import re


class YonhapCrawler(BaseCrawler):
    """
    연합뉴스 기사 목록 + 본문 크롤러.

    페이지 구조:
      - 목록: https://www.yna.co.kr/news/{page_no}
      - 상세 URL 패턴: https://www.yna.co.kr/view/AKR{YYYYMMDDxxxxxxxxxx}
      - 본문 컨테이너: article.story-news article 또는 div.story-news
    """

    def __init__(self):
        super().__init__(source_name="Yonhap")
        self.company = "연합뉴스"
        self.domain = "https://www.yna.co.kr"
        self.list_base = f"{self.domain}/news"

    def crawl(self, limit=10, progress_callback=None):
        self.logger.info(f"Starting crawl for {self.source_name}")
        results = []
        seen = set()

        for page_no in range(1, 5001):
            if len(results) >= limit:
                break

            list_url = f"{self.list_base}/{page_no}"
            response = self.fetch_url(list_url)
            if not response:
                # 일시적 연결 실패는 페이지를 건너뛰고 계속 진행
                self.logger.warning(f"list page {page_no} fetch failed; skipping")
                continue

            soup = BeautifulSoup(response.text, "html.parser")

            # 연합뉴스는 li 내부에 a.tit-news, div.txt-time 등의 구조
            items = soup.select("ul.list01 li") or soup.select("div.list-type038 li")
            if not items:
                # 보다 일반적인 fallback: 'view/AKR' 링크를 가진 모든 li
                items = []
                for a in soup.select('a[href*="/view/AKR"]'):
                    li = a.find_parent("li") or a.find_parent("div")
                    if li:
                        items.append(li)

            if not items:
                break

            recent_count = 0
            dated_count = 0
            for item in items:
                if len(results) >= limit:
                    break

                try:
                    link_elem = item.select_one('a[href*="/view/AKR"]')
                    if not link_elem:
                        continue

                    href = link_elem.get("href", "")
                    detail_url = urllib.parse.urljoin(self.domain, href)

                    m = re.search(r"AKR(\d+)", detail_url)
                    if not m:
                        continue
                    article_id = m.group(1)

                    if detail_url in seen:
                        continue

                    # 제목: a 내부의 tit-news / strong, 없으면 a 자체 텍스트
                    title_elem = (
                        link_elem.select_one(".tit-news")
                        or link_elem.select_one("strong")
                        or link_elem
                    )
                    title = title_elem.get_text(strip=True)
                    if not title:
                        continue

                    # 날짜: txt-time / span.date 등
                    date_elem = (
                        item.select_one(".txt-time")
                        or item.select_one(".date-published")
                        or item.select_one("span.date")
                    )
                    raw_date = date_elem.get_text(strip=True) if date_elem else None

                    # 연합뉴스 AKR ID 앞 8자리가 날짜인 경우가 많음 → 백업으로 사용
                    if not raw_date and len(article_id) >= 8:
                        raw_date = f"{article_id[0:4]}-{article_id[4:6]}-{article_id[6:8]}"

                    if raw_date:
                        dated_count += 1
                    if not self.is_recent_date(raw_date, allow_unknown=False):
                        continue
                    recent_count += 1
                    seen.add(detail_url)

                    detail_data = self.parse_detail(detail_url)

                    unified = self.make_unified_data(
                        title=title,
                        date=raw_date,
                        content=detail_data.get("content") if detail_data else None,
                        url=detail_url,
                        doc_id=f"yonhap_{article_id}",
                        company=self.company,
                        author=detail_data.get("author") if detail_data else None,
                        summary=detail_data.get("summary") if detail_data else None,
                        image_urls=detail_data.get("images", []) if detail_data else [],
                    )
                    results.append(unified)

                    if progress_callback:
                        progress_callback(len(results), limit, f"page {page_no}")
                    self.logger.info(f"Successfully crawled: {title}")

                except Exception as e:
                    self.logger.error(f"Error parsing Yonhap item: {e}")
                    continue

            if dated_count and recent_count == 0:
                break

        return results

    def parse_detail(self, url):
        response = self.fetch_url(url)
        if not response:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # 본문 영역 (연합뉴스 페이지마다 약간씩 다름)
        content_elem = (
            soup.select_one("article.story-news")
            or soup.select_one("div.story-news")
            or soup.select_one("#articleWrap")
            or soup.select_one("article")
        )

        # 작성자: meta[property=article:author] 또는 본문 하단 기자명
        author = None
        meta_author = soup.select_one('meta[property="article:author"]')
        if meta_author and meta_author.get("content"):
            author = meta_author["content"].strip()
        else:
            byline = soup.select_one(".tit-byline") or soup.select_one(".writer-zone01")
            if byline:
                author_text = byline.get_text(" ", strip=True)
                m = re.search(r"([가-힣A-Za-z·\s]+)\s+기자", author_text)
                if m:
                    author = m.group(1).strip() + " 기자"

        # 메타 디스크립션을 요약으로 사용
        summary = None
        meta_desc = soup.select_one('meta[property="og:description"]') or soup.select_one(
            'meta[name="description"]'
        )
        if meta_desc and meta_desc.get("content"):
            summary = meta_desc["content"].strip()

        # 본문 이미지
        images = []
        if content_elem:
            for img in content_elem.select("img"):
                src = img.get("src") or img.get("data-src")
                if src:
                    images.append(urllib.parse.urljoin(self.domain, src))

        return {
            "content": content_elem,
            "author": author,
            "summary": summary,
            "images": images,
        }
