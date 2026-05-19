from bs4 import BeautifulSoup
from src.crawlers.base_crawler import BaseCrawler
import urllib.parse
import re

class KccCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(source_name="KCC")
        self.base_url = "https://www.kcc.go.kr/user.do?boardId=1113&page=A05030000"
        self.domain = "https://www.kcc.go.kr"

    def crawl(self, limit=10):
        self.logger.info(f"Starting crawl for {self.source_name} (Target: {limit})")
        
        # 마지막 수집한 ID 불러오기
        last_id = self.get_last_id()
        if last_id:
            self.logger.info(f"기존 마지막 수집 ID: {last_id}")
        # 한 페이지에 더 많은 게시물을 노출시키기 위해 nop=50 추가
        list_url = f"{self.base_url}&nop=50"
        response = self.fetch_url(list_url)
        if not response:
            return []
 
        soup = BeautifulSoup(response.text, 'html.parser')
        rows = soup.select('table tbody tr')
        
        results = []
        seen_urls = set() # 중복 제거용
        newest_id_candidate = None
        
        for i, row in enumerate(rows):
            if len(results) >= limit:
                break
                
            try:
                # 공지사항 등 번호가 없는 행 제외
                num_elem = row.select_one('td:nth-child(1)')
                if num_elem and '공지' in num_elem.get_text():
                    continue
 
                title_elem = row.select_one('td:nth-child(2) a')
                date_elem = row.select_one('td:nth-child(6)')
                
                if not title_elem or not date_elem:
                    continue
                
                title = title_elem.get_text(strip=True)
                raw_date = date_elem.get_text(strip=True)
                
                # 상세 페이지 URL 결정
                href = title_elem.get('href', '')
                detail_url = self.canonicalize_url(self.normalize_url(href, self.domain))
                
                # [델타 크롤링] href에서 boardSeq 추출 및 중복 체크
                seq_match = re.search(r"boardSeq=(\d+)", href)
                if seq_match:
                    seq = seq_match.group(1)
                    
                    # 마지막으로 수집했던 ID를 만나면 즉시 중단
                    if last_id and str(seq) == str(last_id):
                        self.logger.info(f"마지막 수집 지점({last_id})에 도달했습니다. 크롤링을 종료합니다.")
                        break
                    
                    # 이번 실행에서 가장 최신 ID 저장 (첫 번째 유효한 게시물)
                    if newest_id_candidate is None:
                        newest_id_candidate = seq
                else:
                    self.logger.warning(f"Could not extract boardSeq from href: {href}")

                # 중복 체크
                if detail_url in seen_urls:
                    continue
 
                # 상세 페이지 파싱
                detail_data = self.parse_detail(detail_url)
                if not detail_data:
                    continue
                
                attachment_text = self.process_attachments(detail_data.get('attachments', []))
                
                unified_data = self.make_unified_data(
                    title=title,
                    date=raw_date,
                    content=detail_data['content'],
                    url=detail_url,
                    attachments=detail_data.get('attachments', []),
                    attachment_text=attachment_text,
                    department=detail_data.get('department'),
                    author=detail_data.get('author'),
                    image_urls=detail_data.get('image_urls', []),
                    stable_id=seq,
                )
                
                results.append(unified_data)
                seen_urls.add(detail_url)
                self.logger.info(f"[{len(results)}/{limit}] Successfully crawled: {title}")
                
            except Exception as e:
                self.logger.error(f"Error parsing row: {e}")
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
        
        # KCC 상세 페이지 본문 영역 (정밀 선택자 적용)
        content_elem = soup.select_one('td.table_con') or soup.select_one('.view_content') or soup.find('td', class_='view_content')
        
        if not content_elem:
            # 본문 영역을 못 찾으면 보조 선택자 시도
            content_elem = soup.select_one('.view_cont') or soup.select_one('#contents')
            
        # 첨부파일 추출
        attachments_dict = {}
        # KCC는 보통 'file_list' 또는 특정 클래스 내에 i 태그와 a 태그가 있음
        file_links = soup.select('a[onclick*="fileDownload"], a[href*="fileDownload"], a[href*="download.do"], a.file_download')
        
        for link in file_links:
            onclick = link.get('onclick', '')
            href = link.get('href', '')
            text = link.get_text(strip=True)
            
            # download_url 결정
            download_url = None
            
            # jsessionid 제거하여 URL 정규화
            clean_href = re.sub(r';jsessionid=[^?#]+', '', href)
            
            file_match = re.search(r"fileDownload\('(\w+)',\s*'(.+?)'\)", onclick)
            if file_match:
                file_id = file_match.group(1)
                file_name = file_match.group(2)
                download_url = f"{self.domain}/fileDownload.do?fileId={file_id}"
            elif 'download.do' in clean_href or 'download' in clean_href.lower():
                download_url = self.normalize_url(clean_href, self.domain)
                file_name = text
            elif clean_href and any(ext in clean_href.lower() for ext in ['.hwp', '.pdf', '.hwpx', '.docx']):
                download_url = self.normalize_url(clean_href, self.domain)
                file_name = text
            
            if download_url:
                # URL에서 jsessionid를 제거하고 쿼리 스트링을 정렬하여 키 생성 (결과론적인 데둡)
                parsed = urllib.parse.urlparse(download_url)
                params = urllib.parse.parse_qs(parsed.query)
                # fileSeq 또는 fileId가 있으면 그것을 핵심 키로 사용
                file_seq = params.get('fileSeq', params.get('fileId', [None]))[0]
                dedup_key = file_seq if file_seq else re.sub(r';jsessionid=[^?#]+', '', download_url)
                
                # 파일 확장자 명시 여부 (공백 제거 후 확인)
                file_name = file_name.strip()
                has_ext = any(file_name.lower().endswith(e) for e in ['.hwp', '.pdf', '.hwpx', '.docx'])
                
                # '다운로드'나 '보기' 같은 일반적인 텍스트보다 파일명인 것 같은 텍스트를 우선함
                is_generic = file_name in ["다운로드", "보기", "뷰어보기", "Download"]
                
                if dedup_key not in attachments_dict:
                    attachments_dict[dedup_key] = {
                        "file_name": file_name if file_name else "attachment",
                        "download_url": download_url,
                        "extracted_text": None
                    }
                else:
                    # 기존 이름이 일반적인 명칭이고 신규 이름이 구체적이라면 업데이트
                    current_name = attachments_dict[dedup_key]['file_name']
                    current_is_generic = current_name in ["다운로드", "보기", "뷰어보기", "Download"]
                    if current_is_generic and not is_generic:
                        attachments_dict[dedup_key]['file_name'] = file_name
                    elif not current_name.lower().endswith(('.hwp', '.pdf', '.hwpx', '.docx')) and has_ext:
                        attachments_dict[dedup_key]['file_name'] = file_name
        
        attachments = list(attachments_dict.values())
        
        # 담당부서, 작성자 추출 (보통 th-td 또는 dt-dd 구조)
        department = None
        author = None
        for label in soup.find_all(['th', 'dt', 'span', 'strong']):
            text = label.get_text(strip=True)
            if '담당부서' in text:
                val_elem = label.find_next_sibling(['td', 'dd', 'span'])
                if val_elem: department = val_elem.get_text(strip=True)
            elif '작성자' in text:
                val_elem = label.find_next_sibling(['td', 'dd', 'span'])
                if val_elem: author = val_elem.get_text(strip=True)
        
        # 이미지 추출 (본문 내 이미지)
        image_urls = []
        if content_elem:
            for img in content_elem.select('img'):
                img_src = img.get('src')
                if img_src:
                    full_image_url = urllib.parse.urljoin(url, img_src)
                    if full_image_url not in image_urls:
                        image_urls.append(full_image_url)
        
        return {
            "content": content_elem,
            "attachments": attachments,
            "department": department,
            "author": author,
            "image_urls": image_urls
        }
