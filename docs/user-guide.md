# ASAP Extract — User Guide

대용량 데이터 추출을 안전하게 받기 위한 비동기 API. 한 번에 수백만 행을 받을 수 있도록 만들어졌습니다.

---

## 멘탈 모델: "주문 후 픽업" 패턴

이 API는 **요청을 보내자마자 결과를 받는 동기 방식이 아닙니다.** 데이터 양이 많아 요청·응답이 분리되어 있습니다.

```
1. 추출 요청    →   서버가 "요청 받았어요, 표 드릴게요"라며 job_id 발급
2. 진행 확인    →   주기적으로 "내 거 다 됐어요?" 물어봐서 상태 확인
3. 다운로드     →   완료되면 결과 파일 받기
```

서버는 백그라운드에서 데이터를 추출해 디스크에 파일로 쌓습니다. 사용자는 그 사이 다른 일을 할 수 있고, 네트워크가 끊겨도 잡은 그대로 진행됩니다.

---

## 인증

모든 요청에는 발급받은 API 키가 필요합니다. 키는 **관리자가 발급해서 전달**하며, 형식은 다음과 같습니다.

```
ek_live_<keyid>_<secret>
```

요청 시 HTTP 헤더에 실어 보냅니다.

```
Authorization: Bearer ek_live_...
```

키를 잃어버리면 새로 받아야 합니다. 서버는 평문을 저장하지 않아 복구 불가능.

---

## 엔드포인트

| 메서드 | 경로                                  | 용도                |
| ----- | ----------------------------------- | ----------------- |
| POST   | `/v1/extracts`                      | 추출 요청 (잡 등록)       |
| GET    | `/v1/extracts/{job_id}`             | 잡 상태/메타 조회         |
| GET    | `/v1/extracts/{job_id}/download`    | 완료된 결과 파일 다운로드     |
| DELETE | `/v1/extracts/{job_id}`             | 잡 취소               |

### 1) 추출 요청

```
POST /v1/extracts
Authorization: Bearer ek_live_...
Content-Type: application/json
Idempotency-Key: my-daily-jan-export   # 옵션 — 아래 멱등성 항목 참고

{
  "dataset": "events",
  "filters": {
    "from": "2026-01-01T00:00:00Z",
    "to":   "2026-02-01T00:00:00Z"
  }
}
```

응답:
```
HTTP/1.1 202 Accepted

{
  "job_id": "67a7ccbf-f935-4c89-8db1-9113d637f926",
  "status": "queued",
  "dataset": "events",
  "row_count": 0,
  "bytes": 0,
  ...
}
```

`job_id`를 잘 보관하세요. 이후 모든 조작은 이 ID로.

### 2) 상태 조회

```
GET /v1/extracts/{job_id}
Authorization: Bearer ek_live_...
```

응답:
```
HTTP/1.1 200 OK

{
  "job_id": "67a7ccbf-...",
  "status": "succeeded",
  "dataset": "events",
  "row_count": 100000,
  "bytes": 8166826,
  "file_sha256": "d1ffcea5...",
  "created_at": "2026-05-24T...",
  "started_at": "2026-05-24T...",
  "finished_at": "2026-05-24T...",
  "expires_at": "2026-05-27T...",
  "error_class": null,
  "error_message": null
}
```

상태가 종료 상태(`succeeded`/`failed`/`cancelled`/`expired`)가 될 때까지 폴링합니다.

**상태 의미**

| status      | 의미                                                |
| ----------- | ------------------------------------------------- |
| `queued`    | 등록되었고 워커가 처리하기를 기다리는 중                            |
| `running`   | 워커가 데이터를 가져와 파일에 쓰는 중                              |
| `succeeded` | 완료 → 다운로드 가능. `expires_at` 시각까지 다운로드 보장            |
| `failed`    | 에러로 종료. `error_class` / `error_message` 확인        |
| `cancelled` | 사용자가 DELETE로 취소                                   |
| `expired`   | 보존 기간 초과로 파일 삭제됨 (재요청 필요)                         |

폴링 권장 간격: 처음 1초, 이후 2~5초. 잡 크기가 클 거 같으면 더 길게.

### 3) 다운로드

```
GET /v1/extracts/{job_id}/download
Authorization: Bearer ek_live_...
```

응답:
```
HTTP/1.1 200 OK
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="events-67a7ccbf-...csv"

id,occurred_at,category,user_id,payload
1,2026-01-01T00:00:00,view,85,"{""amount"":60.7,""source"":""ios""}"
...
```

서버는 인증만 처리하고 실제 파일은 Nginx가 흘려줍니다. 큰 파일이라도 안전하게 받을 수 있습니다.

`succeeded` 상태가 아니면 `409 Conflict`. 보존 기간이 지났으면 `404`.

### 4) 취소

```
DELETE /v1/extracts/{job_id}
Authorization: Bearer ek_live_...
```

응답:
```
HTTP/1.1 202 Accepted

{ "status": "cancel_requested" }
```

워커는 다음 배치(기본 10,000행) 경계에서 자체 종료하고 `status=cancelled`로 마감합니다. 즉시는 아니지만 늦어도 한 호흡 안에.

이미 종료된 잡은 그대로 두고 현재 상태를 반환합니다.

---

## 데이터셋과 필터

현재 추출 가능한 데이터셋은 **`events`** 하나입니다 (운영 환경 연결 후 확장 예정).

### `events`

| 컬럼            | 타입           | 설명                                |
| ------------- | ------------ | --------------------------------- |
| `id`          | bigint       | 이벤트 고유 ID                         |
| `occurred_at` | datetime(3)  | 이벤트 발생 시각 (정렬 키)                  |
| `category`    | varchar(64)  | view, click, purchase, signup 등   |
| `user_id`     | bigint       | 사용자 ID                            |
| `payload`     | json         | 부가 정보 (CSV에선 JSON 문자열로 인코딩)       |

**필터**

| 필터          | 필수? | 형식                                            |
| ----------- | --- | --------------------------------------------- |
| `from`      | ✓   | ISO datetime, 포함 (`2026-01-01T00:00:00Z`)     |
| `to`        | ✓   | ISO datetime, **제외**. `from`보다 뒤              |
| `category`  |     | 문자열 리스트. IN 절로 적용. 예: `["purchase","signup"]` |
| `user_id`   |     | bigint 리스트. IN 절로 적용                          |

화이트리스트에 없는 필드를 넣으면 **`422`**. 안전을 위한 의도된 동작이며, 새 필터가 필요하면 관리자에게 요청하세요.

**예시: 1월 결제/가입만**

```
POST /v1/extracts
Authorization: Bearer ek_live_...
Content-Type: application/json

{
  "dataset": "events",
  "filters": {
    "from":     "2026-01-01T00:00:00Z",
    "to":       "2026-02-01T00:00:00Z",
    "category": ["purchase", "signup"]
  }
}
```

---

## 멱등성 (안전한 재시도)

네트워크가 불안정하거나 배치 스크립트가 같은 요청을 두 번 보낼 위험이 있을 때, `Idempotency-Key` 헤더로 안전망을 칩니다.

```
POST /v1/extracts
Idempotency-Key: daily-export-2026-01-25
Content-Type: application/json
...
```

같은 키 + 같은 `Idempotency-Key` 조합은 **항상 동일한 잡을 반환**합니다. 클라이언트는 안심하고 재시도 가능. 잡이 두 번 생기지 않습니다.

키는 자유 문자열. 비즈니스 의미가 있는 키(`daily-export-2026-01-25`)도 좋고 UUID도 좋습니다.

---

## 한도와 제약

| 항목                  | 값 (기본)         | 의미                                           |
| ------------------- | ------------- | -------------------------------------------- |
| 한 키의 동시 진행 잡 수 (in-flight) | 5         | queued+running 합산. 넘으면 `429` + `Retry-After`. |
| 한 잡의 최대 행 수          | 50,000,000   | 넘으면 `failed` (`ExtractTooLarge`). 필터 좁히기.    |
| 결과 파일 보존 기간          | 72시간          | 이후 `expired`. 자주 받는 데이터면 보존 기간 안에 끌어가세요.     |

---

## 에러와 대처

| 코드                                  | 원인 / 대처                                                |
| ----------------------------------- | ----------------------------------------------------- |
| `401 Unauthorized`                  | 키 누락 / 잘못된 키 / 만료된 키 / 폐기된 키. 관리자에게 새 키 요청.            |
| `403 Forbidden`                     | 이 키는 해당 dataset에 접근 권한 없음. 관리자에게 권한 요청.                |
| `404 Not Found`                     | job_id 오타 / 본인 키로 만들지 않은 잡 / 보존 기간 초과.                 |
| `409 Conflict`                      | 다운로드를 `succeeded` 아닌 상태에서 시도. 폴링 먼저.                  |
| `422 Unprocessable Entity`          | 필터 누락 / 알 수 없는 필드 / 형식 오류. 응답 `detail` 메시지 확인.        |
| `429 Too Many Requests`             | in-flight cap 초과. `Retry-After`(초) 만큼 대기 후 재시도.        |
| `5xx`                               | 서버 측 문제. 잠시 후 재시도하거나 관리자에게 보고.                        |

`failed` 상태일 때 응답의 `error_class`:

| error_class           | 의미                                              |
| --------------------- | ----------------------------------------------- |
| `ExtractTooLarge`     | 결과가 최대 행 수 한도 초과. 필터를 좁히세요.                     |
| `OperationalError`    | 원격 DB 일시적 문제. 잠시 후 재요청.                          |
| 그 외                   | `error_message` 확인 후 관리자에게.                     |

---

## Swagger UI (인터랙티브 탐색)

`http://<host>/docs` (로컬: `http://localhost:8080/docs`)에서 OpenAPI 문서를 인터랙티브로 탐색할 수 있습니다.

1. 우측 상단 **"Authorize"** 클릭
2. API 키 붙여넣기 (`ek_live_...`)
3. 각 엔드포인트 "Try it out" → JSON 입력 → 실행

빠르게 동작을 확인하기에 편합니다.
