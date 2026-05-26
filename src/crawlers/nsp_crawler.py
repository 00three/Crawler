import json
from bs4 import BeautifulSoup
from src.crawlers.base_crawler import BaseCrawler
import urllib.parse
import re

class NspCrawler(BaseCrawler):
    def __init__(self):
        # 국립국회도서관은 'National Assembly' 소스로 설정
        super().__init__(source_name="National Assembly")
        self.api_url = "https://nsp.nanet.go.kr/search/searchInnerList.do"
        self.detail_base_url = "https://nsp.nanet.go.kr/trend/latest/detail.do"

    def crawl(self, limit=25):
        self.logger.info(f"Starting crawl for {self.source_name} (Goal: {limit})")
        
        # 마지막 수집한 ID 불러오기
        last_id = self.get_last_id()
        if last_id:
            self.logger.info(f"기존 마지막 수집 ID: {last_id}")
        
        payload = {
            "collection": "trend",
            "query": "",
            "listCount": str(limit + 5), # 필터링 대비 약간 넉넉히
            "startCount": 0
        }
        
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://nsp.nanet.go.kr/trend/latest/list.do"
        }

        response = self.fetch_url(self.api_url, method="POST", json=payload, headers=headers)
        if not response:
            return []

        try:
            data = response.json()
            items = data.get('searchResultMap', {}).get('searchResultList', [])
        except Exception as e:
            self.logger.error(f"Failed to parse JSON response: {e}")
            return []
        
        results = []
        press_release_count = 0
        seen_ids = set() # 중복 방지용 고유 번호 (idxno 역할)
        newest_id_candidate = None
        
        for i, item in enumerate(items):
            if press_release_count >= limit:
                break
                
            try:
                # 고유 번호 추출
                control_no = item.get('latestTrendControlNo')
                
                # [델타 크롤링] 마지막으로 수집했던 ID를 만나면 즉시 중단
                if last_id and str(control_no) == str(last_id):
                    self.logger.info(f"마지막 수집 지점({last_id})에 도달했습니다. 크롤링을 종료합니다.")
                    break
                
                # 이번 실행에서 가장 최신 ID 저장 (목록의 첫 번째 아이템)
                if i == 0:
                    newest_id_candidate = control_no

                if not control_no or control_no in seen_ids:
                    continue
                
                title = item.get('title')
                publish_date = item.get('publishDt')
                detail_url = f"{self.detail_base_url}?latestTrendControlNo={control_no}&listChk=list"
                
                # 상세 페이지로 이동하여 크롤링
                detail_data = self.parse_detail(detail_url)
                if not detail_data:
                    continue
                
                # 해시태그 수집
                hashtags_str = item.get('hashtag', '')
                hashtag_list = [t.strip() for t in hashtags_str.split(',') if t.strip()] if hashtags_str else []
                
                unified_data = self.make_unified_data(
                    title=title,
                    date=publish_date,
                    content=detail_data['content'],
                    url=detail_url,
                    summary=hashtags_str if hashtags_str else None,
                    hashtags=hashtag_list,
                    attachments=detail_data.get('attachments', []),
                    image_urls=detail_data.get('image_urls', []),
                    references=detail_data.get("references", []),
                    stable_id=control_no,
                )

                results.append(unified_data)
                press_release_count += 1
                results.extend(
                    self.crawl_reference_articles(
                        detail_data.get("references", []),
                        parent_doc_id=unified_data["doc_id"],
                    )
                )
                seen_ids.add(control_no)
                self.logger.info(f"[{press_release_count}/{limit}] Successfully crawled: {title}")
                
            except Exception as e:
                self.logger.error(f"Error processing item: {e}")
                continue
                
        # 수집된 데이터가 있다면 마지막 수집 ID 업데이트
        if newest_id_candidate:
            self.update_last_id(newest_id_candidate)
            
        return results

    def parse_detail(self, url):
        response = self.fetch_url(url)
        if not response:
            return None
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 상세 본문 추출
        content_elem = soup.select_one('.post_cont_area_editor') or soup.select_one('.se-viewer')
        if not content_elem:
            content_elem = soup.select_one('.contents') or soup.select_one('.view_cont')
            
        content_text = ""
        image_urls = []
        
        if content_elem:
            # 텍스트 추출
            content_text = content_elem.get_text(separator='\n', strip=True)
            # 이미지 추출
            for img in content_elem.select('img'):
                img_src = img.get('src') or img.get('data-src')
                if img_src:
                    image_urls.append(self.normalize_url(img_src, url))

        # 첨부파일 추출
        attachments = []
        file_links = soup.select('a[href*="fileDownload"], a[href*=".pdf"]')

        seen_file_urls = set()
        for link in file_links:
            href = link.get('href', '')
            if href:
                full_url = self.normalize_url(href, url)
                if full_url in seen_file_urls:
                    continue
                seen_file_urls.add(full_url)
                
                attachments.append({
                    "file_name": link.get_text(strip=True) or "Download",
                    "download_url": full_url
                })

        references = []
        seen_reference_urls = set()
        for link in soup.select("ul.ref_list_area a.data"):
            href = link.get("href", "")
            if not href:
                continue
            full_url = self.canonicalize_url(self.normalize_url(href, url))
            if not full_url or full_url in seen_reference_urls:
                continue
            seen_reference_urls.add(full_url)
            references.append({
                "ref_title": link.get_text(strip=True) or full_url,
                "ref_url": full_url,
            })
        
        return {
            "content": content_text,
            "attachments": attachments,
            "image_urls": image_urls,
            "references": references,
        }

    def crawl_reference_articles(self, references, parent_doc_id, max_depth=2, max_pages=10):
        """NSP 외부 참고 링크를 독립 문서로 수집합니다."""
        results = []
        queue = [
            (ref.get("ref_url"), 1)
            for ref in references
            if ref.get("ref_url")
        ]
        visited = set()

        while queue and len(results) < max_pages:
            url, depth = queue.pop(0)
            canonical_url = self.canonicalize_url(url)
            if not canonical_url or canonical_url in visited:
                continue
            visited.add(canonical_url)

            article = self.parse_reference_article(canonical_url)
            if not article:
                continue

            results.append(
                self.make_unified_data(
                    title=article["title"],
                    date=article.get("date"),
                    content=article["content"],
                    url=canonical_url,
                    author=article.get("author"),
                    image_urls=article.get("image_urls", []),
                    references=article.get("references", []),
                    document_kind="reference_article",
                    parent_doc_id=parent_doc_id,
                    stable_id=canonical_url,
                    source_override=article.get("source"),
                )
            )

            if depth >= max_depth:
                continue

            for next_url in article.get("links", []):
                canonical_next = self.canonicalize_url(next_url)
                if canonical_next and canonical_next not in visited:
                    queue.append((canonical_next, depth + 1))

        return results

    def parse_reference_article(self, url):
        response = self.fetch_url(url)
        if not response:
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        title = self._meta_content(soup, "property", "og:title")
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else None
        if not title and soup.title:
            title = soup.title.get_text(strip=True)

        content_elem = (
            soup.find("article")
            or soup.select_one('[itemprop="articleBody"]')
            or soup.select_one(".article-body")
            or soup.select_one(".article_view")
            or soup.select_one(".article-body-content")
            or soup.select_one(".news_end")
            or soup.select_one("#article-view-content-div")
        )
        if not title or not content_elem:
            return None

        content = self.clean_text(content_elem)
        if len(content) < 80:
            return None

        date = (
            self._meta_content(soup, "property", "article:published_time")
            or self._meta_content(soup, "name", "date")
            or self._meta_content(soup, "name", "pubdate")
        )
        time_tag = soup.find("time")
        if not date and time_tag:
            date = time_tag.get("datetime") or time_tag.get_text(strip=True)

        author = (
            self._meta_content(soup, "name", "author")
            or self._meta_content(soup, "property", "article:author")
        )

        image_urls = []
        for img in content_elem.select("img"):
            src = img.get("src") or img.get("data-src")
            if src:
                image_urls.append(self.normalize_url(src, url))

        links = []
        references = []
        for link in content_elem.select("a[href]"):
            href = self.normalize_url(link.get("href"), url)
            if not href or not re.match(r"^https?://", href):
                continue
            canonical_href = self.canonicalize_url(href)
            if canonical_href == self.canonicalize_url(url):
                continue
            links.append(canonical_href)
            references.append({
                "ref_title": link.get_text(strip=True) or canonical_href,
                "ref_url": canonical_href,
            })

        return {
            "title": title,
            "date": date,
            "author": author,
            "source": (
                self._meta_content(soup, "property", "og:site_name")
                or urllib.parse.urlparse(url).netloc.replace("www.", "")
            ),
            "content": content,
            "image_urls": list(dict.fromkeys(filter(None, image_urls))),
            "links": list(dict.fromkeys(links)),
            "references": references,
        }

    @staticmethod
    def _meta_content(soup, attr_name, attr_value):
        tag = soup.find("meta", attrs={attr_name: attr_value})
        return tag.get("content") if tag else None
