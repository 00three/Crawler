from bs4 import BeautifulSoup
from src.crawlers.base_crawler import BaseCrawler
import urllib.parse
import re


class MediatodayCrawler(BaseCrawler):
    """
    미디어오늘 기사 목록 + 본문 크롤러.

    페이지 구조 (엔디소프트 CMS 기반, 언론노조와 유사):
      - 목록: https://www.mediatoday.co.kr/news/articleList.html?view_type=sm&page=N
      - 상세: https://www.mediatoday.co.kr/news/articleView.html?idxno=XXXXXX
      - 본문 컨테이너: #article-view-content-div
    """

    def __init__(self, section_code=None):
        super().__init__(source_name="Mediatoday")
        self.company = "미디어오늘"
        self.domain = "https://www.mediatoday.co.kr"
        # section_code 예: 'S1N2'(정치), 'S1N4'(사회), None 이면 전체 기사
        self.section_code = section_code
        self.base_url = f"{self.domain}/news/articleList.html"

    def _build_list_url(self, page_no):
        params = {"view_type": "sm", "page": str(page_no)}
        if self.section_code:
            params["sc_section_code"] = self.section_code
        return f"{self.base_url}?{urllib.parse.urlencode(params)}"

    def crawl(self, limit=10, progress_callback=None):
        self.logger.info(f"Starting crawl for {self.source_name}")
        results = []
        seen = set()

        for page_no in range(1, 5001):
            if len(results) >= limit:
                break

            list_url = self._build_list_url(page_no)
            response = self.fetch_url(list_url)
            if not response:
                break

            soup = BeautifulSoup(response.text, "html.parser")

            # 각 기사 블록은 'articleView.html?idxno=' 링크를 포함한 컨테이너
            article_links = soup.select('a[href*="articleView.html?idxno="]')
            article_blocks = []
            seen_idxno_on_page = set()
            for link in article_links:
                href = link.get("href", "")
                m = re.search(r"idxno=(\d+)", href)
                if not m:
                    continue
                idxno = m.group(1)
                if idxno in seen_idxno_on_page:
                    continue
                seen_idxno_on_page.add(idxno)
                # 같은 기사의 부모 블록을 찾기 (li / div.list 등)
                block = link.find_parent(["li", "div", "section"])
                if block:
                    article_blocks.append((idxno, block))

            if not article_blocks:
                break

            recent_count = 0
            dated_count = 0
            for idxno, block in article_blocks:
                if len(results) >= limit:
                    break

                try:
                    title_elem = block.select_one(
                        f'a[href*="idxno={idxno}"]'
                    )
                    if not title_elem:
                        continue
                    title = title_elem.get_text(strip=True)
                    if not title:
                        continue

                    # 날짜는 보통 블록 내 em/span 에 "YYYY.MM.DD HH:MM" 형식으로 들어있음
                    date_text = None
                    for elem in block.find_all(["em", "span", "i"]):
                        text = elem.get_text(strip=True)
                        if re.search(r"\d{4}[./-]\d{2}[./-]\d{2}", text):
                            date_text = text
                            break
                    if not date_text:
                        # 블록 전체 텍스트에서 정규식으로 날짜 추출 시도
                        m = re.search(
                            r"\d{4}[./-]\d{2}[./-]\d{2}(?:\s+\d{2}:\d{2})?",
                            block.get_text(" ", strip=True),
                        )
                        if m:
                            date_text = m.group(0)

                    if date_text:
                        dated_count += 1
                    if not self.is_recent_date(date_text, allow_unknown=True):
                        continue
                    recent_count += 1

                    detail_url = (
                        f"{self.domain}/news/articleView.html?idxno={idxno}"
                    )
                    if detail_url in seen:
                        continue
                    seen.add(detail_url)

                    detail_data = self.parse_detail(detail_url)

                    unified = self.make_unified_data(
                        title=title,
                        date=date_text,
                        content=detail_data.get("content") if detail_data else None,
                        url=detail_url,
                        doc_id=f"mediatoday_{idxno}",
                        company=self.company,
                        author=detail_data.get("author") if detail_data else None,
                        summary=detail_data.get("summary") if detail_data else None,
                        image_urls=detail_data.get("images", []) if detail_data else [],
                        attachments=detail_data.get("attachments", []) if detail_data else [],
                    )
                    results.append(unified)

                    if progress_callback:
                        progress_callback(len(results), limit, f"page {page_no}")
                    self.logger.info(f"Successfully crawled: {title}")

                except Exception as e:
                    self.logger.error(f"Error parsing Mediatoday block: {e}")
                    continue

            # 페이지에 날짜가 있는데 최근 글이 하나도 없으면 더 깊이 들어가지 않음
            if dated_count and recent_count == 0:
                break

        return results

    def parse_detail(self, url):
        response = self.fetch_url(url)
        if not response:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # 본문
        content_elem = (
            soup.select_one("#article-view-content-div")
            or soup.select_one("article")
            or soup.select_one(".article-view-content")
        )

        # 작성자
        author = None
        author_elem = soup.select_one(".no-byline") or soup.select_one(
            "ul.no-bylines li"
        )
        if author_elem:
            author_text = author_elem.get_text(" ", strip=True)
            m = re.search(r"([가-힣A-Za-z·\s]+)\s+기자", author_text)
            if m:
                author = m.group(1).strip() + " 기자"

        # 요약/부제
        summary = None
        sub_elem = soup.select_one(".article-head-sub-title") or soup.select_one(
            ".article-head .article-head-sub"
        )
        if sub_elem:
            summary = sub_elem.get_text(strip=True)

        # 본문 내 이미지
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
            "attachments": [],
        }
