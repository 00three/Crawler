from bs4 import BeautifulSoup
from src.crawlers.base_crawler import BaseCrawler
import urllib.parse
import re
import json


class HankyorehCrawler(BaseCrawler):
    """
    한겨레 기사 목록 + 본문 크롤러.

    한겨레는 섹션별 목록 페이지를 사용:
      - 정치:   https://www.hani.co.kr/arti/politics
      - 사회:   https://www.hani.co.kr/arti/society
      - 경제:   https://www.hani.co.kr/arti/economy
      - 문화:   https://www.hani.co.kr/arti/culture
      - 미디어: https://www.hani.co.kr/arti/society/media
      - 전체:   https://www.hani.co.kr/arti/list.html

    상세 URL 패턴: https://www.hani.co.kr/arti/{section}/{subsection}/{article_id}.html
    """

    DEFAULT_SECTIONS = [
        ("politics", "politics"),
        ("society", "society"),
        ("society/media", "media"),
    ]

    def __init__(self, sections=None):
        super().__init__(source_name="Hankyoreh")
        self.company = "한겨레"
        self.domain = "https://www.hani.co.kr"
        # [(path, label), ...]
        self.sections = sections or self.DEFAULT_SECTIONS

    def crawl(self, limit=10, progress_callback=None):
        self.logger.info(f"Starting crawl for {self.source_name}")
        results = []
        seen = set()
        per_section = max(1, limit // len(self.sections))

        for section_path, section_label in self.sections:
            section_count = 0
            for page_no in range(1, 2001):
                if len(results) >= limit or section_count >= per_section:
                    break

                list_url = f"{self.domain}/arti/{section_path}?page={page_no}"
                response = self.fetch_url(list_url)
                if not response:
                    break

                soup = BeautifulSoup(response.text, "html.parser")

                # 한겨레 기사 링크 패턴: /arti/.../숫자.html
                article_links = []
                seen_ids_on_page = set()
                for a in soup.select('a[href*="/arti/"]'):
                    href = a.get("href", "")
                    m = re.search(r"/arti/[^?#]*?/(\d+)\.html", href)
                    if not m:
                        continue
                    article_id = m.group(1)
                    if article_id in seen_ids_on_page:
                        continue
                    seen_ids_on_page.add(article_id)
                    article_links.append((article_id, a))

                if not article_links:
                    break

                recent_count = 0
                dated_count = 0
                for article_id, link in article_links:
                    if (
                        len(results) >= limit
                        or section_count >= per_section
                    ):
                        break

                    try:
                        href = link.get("href", "")
                        detail_url = urllib.parse.urljoin(self.domain, href)
                        if detail_url in seen:
                            continue

                        title = link.get_text(strip=True)
                        if not title or len(title) < 5:
                            # 빈 a (이미지 only) 인 경우엔 옆에 있는 텍스트 링크에 의존
                            continue

                        # 목록에서는 날짜를 못 가져오는 경우가 많아 상세에서 확인
                        detail_data = self.parse_detail(detail_url)
                        if not detail_data:
                            continue
                        raw_date = detail_data.get("date")
                        if raw_date:
                            dated_count += 1
                        if not self.is_recent_date(raw_date, allow_unknown=False):
                            continue
                        recent_count += 1
                        seen.add(detail_url)
                        section_count += 1

                        unified = self.make_unified_data(
                            title=detail_data.get("title") or title,
                            date=raw_date,
                            content=detail_data.get("content"),
                            url=detail_url,
                            doc_id=f"hankyoreh_{article_id}",
                            company=self.company,
                            department=section_label,
                            author=detail_data.get("author"),
                            summary=detail_data.get("summary"),
                            image_urls=detail_data.get("images", []),
                        )
                        results.append(unified)

                        if progress_callback:
                            progress_callback(
                                len(results), limit, f"{section_label} p{page_no}"
                            )
                        self.logger.info(f"Successfully crawled: {title}")

                    except Exception as e:
                        self.logger.error(f"Error parsing Hankyoreh item: {e}")
                        continue

                if dated_count and recent_count == 0:
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
            soup.select_one("h3.title")
            or soup.select_one(".article-head h1")
            or soup.select_one("h1")
        )
        if title_elem:
            title = title_elem.get_text(strip=True)
        else:
            og_title = soup.select_one('meta[property="og:title"]')
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()

        # 날짜: __NEXT_DATA__ JSON 의 article.createDate 가 가장 안정적
        # (한겨레가 Next.js로 전환된 뒤 meta[article:published_time] 사라짐)
        raw_date = None
        nd = soup.find("script", id="__NEXT_DATA__")
        if nd and nd.string:
            try:
                data = json.loads(nd.string)
                art = data.get("props", {}).get("pageProps", {}).get("article", {}) or {}
                raw_date = art.get("createDate") or art.get("publishDate") or art.get("updateDate")
            except Exception:
                pass
        if not raw_date:
            meta_date = soup.select_one('meta[property="article:published_time"]')
            if meta_date and meta_date.get("content"):
                raw_date = meta_date["content"]
        if not raw_date:
            # 본문 영역의 dateListItem 클래스
            date_elem = (
                soup.select_one('[class*="dateListItem"] span')
                or soup.select_one(".article-date")
                or soup.select_one(".date")
            )
            if date_elem:
                raw_date = date_elem.get_text(strip=True)

        # 본문
        content_elem = (
            soup.select_one(".article-text")
            or soup.select_one("div.text")
            or soup.select_one("article")
            or soup.select_one("#a-left-scroll-in")
        )

        # 작성자
        author = None
        meta_author = soup.select_one('meta[property="article:author"]') or soup.select_one(
            'meta[name="author"]'
        )
        if meta_author and meta_author.get("content"):
            author = meta_author["content"].strip()
        else:
            byline = soup.select_one(".article-byline") or soup.select_one(".reporter")
            if byline:
                m = re.search(r"([가-힣A-Za-z·\s]+)\s+기자", byline.get_text(" ", strip=True))
                if m:
                    author = m.group(1).strip() + " 기자"

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
                src = img.get("src") or img.get("data-src")
                if src:
                    images.append(urllib.parse.urljoin(self.domain, src))

        return {
            "title": title,
            "date": raw_date,
            "content": content_elem,
            "author": author,
            "summary": summary,
            "images": images,
        }
