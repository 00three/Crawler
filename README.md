# Crawler

4개 소스(`KCC`, `NSP`, `MBC`, `Media Union`)를 수집해 회차별 JSONL 배치를 만드는 크롤러입니다.
`Full-Stack` 레포와는 분리 유지하고, Docker Compose에서 shared volume으로 연결합니다.

## 실행 흐름

```text
10분 스케줄
  -> 4개 크롤러 병렬 실행
  -> batch_YYYYMMDDTHHMMSSffffffZ.jsonl 생성
  -> shared outbox에 저장
  -> Full-Stack의 ingest-worker가 DB 적재
```

기본 수집 주기는 `CRAWLER_INTERVAL_MINUTES=10`입니다.
상태 파일은 `data/states/`에 소스별로 저장되며, 각 크롤러는 마지막 ID 이후 새 문서만 가져옵니다.

## 출력 스키마

```json
{
  "doc_id": "kcc_20260421_0eaf3e36",
  "source": "KCC",
  "document_kind": "press_release",
  "parent_doc_id": null,
  "department": "사무처",
  "author": "임현주",
  "title": "이동통신 3사 위치정보, 정확도 개선됐다",
  "date": "2026-04-21",
  "summary": null,
  "content_text": "...",
  "attachment_text": "...",
  "detail_url": "https://www.kcc.go.kr/user.do?...",
  "image_urls": [],
  "attachments": [],
  "hashtags": [],
  "references": [],
  "crawled_at": "2026-04-21T12:24:32Z"
}
```

- `document_kind`
  - `press_release`: 원 보도자료
  - `reference_article`: 외부 참고기사
- `parent_doc_id`
  - 외부 참고기사가 어떤 원 보도자료에서 발견됐는지 연결합니다.

## 중복 방지

- 결과는 하루 단일 append 파일이 아니라 **회차별 batch 파일**로 저장합니다.
- 저장 직전 `doc_id` 기준으로 한 번 더 dedupe합니다.
- `KCC`는 URL 해시 대신 안정적인 `boardSeq`를 `doc_id` seed로 사용합니다.
- URL canonicalization에서 `jsessionid`와 UTM 파라미터를 제거합니다.

## 외부 참고기사

`NSP`의 외부 링크는 첨부파일과 분리합니다.

- 직접 링크뿐 아니라 본문 내 링크를 재귀 수집합니다.
- 재귀 깊이는 최대 `2`
- 원 보도자료 1건당 최대 `10`개 페이지
- 수집한 외부 기사는 `reference_article` 독립 문서로 저장합니다.

## 로컬 실행

단독 실행:

```bash
pip install -r requirements.txt
python3 main.py --mode manual --limit 5
python3 main.py --mode schedule
```

공식 통합 실행은 `Full-Stack` 레포의 `docker compose up --build`를 사용합니다.
이 레포는 compose에서 `crawler` 서비스의 build context로 사용됩니다.
