# ASAP Extract API

대용량 데이터 추출 API. **요청을 던지면 백그라운드에서 추출이 돌아가고, 끝나면 파일로 다운로드**하는 방식.
한 번에 수백만 행을 메모리에 올리지 않고 안전하게 빼내는 것이 목적.

---

## 핵심 흐름

```
1) POST /v1/extracts          → 즉시 {job_id, status: "queued"} 응답
2) GET  /v1/extracts/{id}     → status가 "succeeded"가 될 때까지 폴링
3) GET  /v1/extracts/{id}/download  → CSV 파일 다운로드
```

"내가 요청을 보내고 결과가 끝날 때까지 기다리는" 동기 API가 **아닙니다**.
요청은 즉시 끝나고, 데이터 추출은 워커가 백그라운드에서 진행합니다.
클라이언트는 폴링(또는 나중에 webhook)으로 완료 여부를 확인한 뒤 다운로드합니다.

---

## 빠른 시작 (로컬, 5분)

```bash
git clone git@github.com:Seunghak-Kwak/asap-extract-api.git
cd asap-extract-api
cp .env.example .env                 # 그대로 두면 됨

docker compose -f deploy/docker-compose.yml up -d --build
# 초기 1회: 마이그레이션 + 시드 데이터(10만 행) + bootstrap API key 자동 처리
```

띄워지면 `http://localhost:8080`에 API가 떠 있습니다.

```bash
curl http://localhost:8080/healthz
# ok
```

**기본 API 키** (로컬 dev에서만 사용):
```
ek_live_devtest_devtestdevtestdevtestdevtestde
```

---

## API 사용법

이후 모든 요청에는 `Authorization: Bearer <키>` 헤더가 필요합니다.

편의를 위해:
```bash
export API=http://localhost:8080
export KEY="ek_live_devtest_devtestdevtestdevtestdevtestde"
```

### 1) 추출 요청

```bash
curl -X POST $API/v1/extracts \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "events",
    "filters": {
      "from": "2026-01-01T00:00:00Z",
      "to":   "2026-02-01T00:00:00Z"
    }
  }'
```

응답:
```json
{
  "job_id": "67a7ccbf-f935-4c89-8db1-9113d637f926",
  "status": "queued",
  "dataset": "events",
  "row_count": 0,
  "bytes": 0,
  ...
}
```

`job_id`를 잘 챙기세요. 다음 단계에서 필요합니다.

### 2) 진행 확인

```bash
curl $API/v1/extracts/<job_id> -H "Authorization: Bearer $KEY"
```

`status` 값이 다음 중 하나로 바뀝니다:

| status        | 의미                                                  |
| ------------- | --------------------------------------------------- |
| `queued`      | 대기 중                                                |
| `running`     | 워커가 추출 중                                            |
| `succeeded`   | 완료 → 다운로드 가능                                       |
| `failed`      | 에러로 종료 (`error_class`, `error_message` 참고)         |
| `cancelled`   | DELETE 호출로 취소됨                                     |
| `expired`     | 보존 기간 초과로 파일 삭제됨 (기본 72시간)                          |

폴링 예시:
```bash
JOB=67a7ccbf-...
while true; do
  s=$(curl -s $API/v1/extracts/$JOB -H "Authorization: Bearer $KEY" \
        | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
  echo "$s"
  [ "$s" = "succeeded" ] && break
  [ "$s" = "failed" ] && break
  sleep 1
done
```

### 3) 결과 다운로드

```bash
curl -O -J $API/v1/extracts/<job_id>/download \
  -H "Authorization: Bearer $KEY"
# 파일명: events-<job_id>.csv  로 저장됨
```

`-O -J`는 서버가 보내준 `Content-Disposition`의 파일명을 그대로 쓰겠다는 옵션.

다운로드는 FastAPI가 인증만 처리하고 **Nginx가 직접 파일을 흘려줍니다** (X-Accel-Redirect).
대용량 파일이어도 Python 프로세스를 점유하지 않습니다.

### 4) 취소

추출이 너무 오래 걸리거나 잘못 요청했다 싶으면:
```bash
curl -X DELETE $API/v1/extracts/<job_id> -H "Authorization: Bearer $KEY"
```

워커는 다음 배치(기본 10000행) 경계에서 자체 종료하고 `status=cancelled`로 마감합니다.

---

## 사용 가능한 데이터셋

### `events`

| 컬럼            | 타입           | 설명                              |
| ------------- | ------------ | ------------------------------- |
| `id`          | bigint       | 이벤트 고유 ID                       |
| `occurred_at` | datetime(3)  | 이벤트 발생 시각 (정렬 키)                |
| `category`    | varchar(64)  | view, click, purchase, signup 등 |
| `user_id`     | bigint       | 사용자 ID                          |
| `payload`     | json         | 부가 정보 (CSV에선 JSON 문자열로 인코딩)     |

**필터 규칙**

| 필터          | 필수 여부 | 형식                                            |
| ----------- | ----- | --------------------------------------------- |
| `from`      | 필수    | ISO datetime (`"2026-01-01T00:00:00Z"`)       |
| `to`        | 필수    | ISO datetime, `from`보다 뒤                      |
| `category`  | 옵션    | 문자열 리스트 (`["purchase","signup"]`) — IN 절로 적용  |
| `user_id`   | 옵션    | bigint 리스트 — IN 절로 적용                         |

화이트리스트에 없는 필드를 넣으면 `422 Unprocessable Entity`로 거절됩니다. 안전을 위한 의도된 동작.

**예시: 1월 결제/가입만**

```bash
curl -X POST $API/v1/extracts \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "events",
    "filters": {
      "from": "2026-01-01T00:00:00Z",
      "to":   "2026-02-01T00:00:00Z",
      "category": ["purchase", "signup"]
    }
  }'
```

---

## 새 데이터셋 추가하는 법

추가는 **단 한 곳만 수정**합니다.

[app/extract/registry.py](app/extract/registry.py)에 항목을 추가하세요:

```python
ORDERS = Dataset(
    name="orders",
    table="orders",
    columns=["id", "ordered_at", "customer_id", "amount", "status"],
    sort_columns=["ordered_at", "id"],   # 소스 DB에 복합 인덱스가 있어야 함
    required_filters=["from", "to"],
    optional_filters=["status", "customer_id"],
    list_filters={"status", "customer_id"},
)

REGISTRY = {
    EVENTS.name: EVENTS,
    ORDERS.name: ORDERS,        # ← 추가
}
```

소스 테이블에 keyset 정렬 키(`(ordered_at, id)`) 인덱스가 반드시 있어야 합니다.
인덱스가 없으면 추출이 풀스캔으로 떨어져 메모리/시간이 폭발합니다.

추가 후 라우터나 워커는 손댈 필요 없습니다.

---

## 관리자 패널

http://localhost:8080/admin 에 접속해서 admin 키로 로그인하면:

- **Overview**: 상태별 잡 카운트, 활성 키 수, 최근 24시간 잡, 누적 추출 행 수, **키별 in-flight cap**
- **Issue API key**: 라벨/datasets/만료일/admin 여부를 입력해 즉시 발급. `full_key`는 발급 직후 한 번만 노출.
- **Recent extracts**: API 키별 / 상태별 / 데이터셋별 필터로 추출 이력 조회. **각 행에 사용자가 보낸 `filters` JSON이 보임** — 어떤 기간/카테고리로 뽑았는지 한눈에.

키는 페이지 sessionStorage에만 보관되며, 탭을 닫으면 사라집니다.

## Rate limit — 키별 in-flight cap

같은 키가 큐를 폭주시키는 것을 막기 위해 **한 키가 동시에 `queued + running` 상태로 가질 수 있는 잡 수**에 상한이 있습니다 (기본 `5`, `.env`의 `EXTRACT_MAX_INFLIGHT_PER_KEY`로 변경).

상한을 넘으면:
```
HTTP/1.1 429 Too Many Requests
Retry-After: 10
{"detail":"in-flight cap reached (5/5); wait for jobs to finish"}
```

잡이 끝나거나 cancel되면 카운터가 떨어지면서 자동 회복. 클라이언트 측에서는 `Retry-After` 만큼 대기 후 재시도하면 됩니다.

> 소프트 캡입니다 — 동시 POST 두 개가 같은 순간에 카운트를 읽으면 일시적으로 cap+1까지 될 수 있어요. RPS 단위의 강한 제한이 필요해지면 별도 미들웨어/리버스 프록시 레벨에서 추가.

## API 키 관리 (관리자 전용)

키는 사용자가 직접 발급 신청하는 게 아니라 **관리자가 발급해서 전달**합니다.
패널 외에 CLI/스크립트에서 쓰려면 아래 엔드포인트를 직접 호출하세요.

### 새 키 발급

```bash
curl -X POST $API/v1/admin/api-keys \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "label": "data-team-jan",
    "datasets": ["events"],
    "expires_in_days": 90,
    "is_admin": false
  }'
```

응답:
```json
{
  "key_id": "4d8963e5",
  "full_key": "ek_live_4d8963e5_...",   ← 이 응답에서만 노출, 즉시 안전한 곳에 저장
  "label": "data-team-jan",
  "datasets": ["events"],
  "is_admin": false,
  "created_at": "2026-05-24T...",
  "expires_at": "2026-08-22T..."
}
```

`full_key`는 **이 응답에서만 보입니다.** 서버는 Argon2id 해시만 보관합니다.

### 키 권한 모델

| 필드          | 의미                                                                  |
| ----------- | ------------------------------------------------------------------- |
| `datasets`  | 추출 가능한 데이터셋 화이트리스트. `["events"]`, `["events","orders"]`, `["*"]`(전체) |
| `is_admin`  | true면 `/v1/admin/*` 호출 가능 — 키 발급/조회/폐기 권한                            |
| `expires_in_days` | 만료까지 일수. 생략하면 무기한.                                              |

스코프 밖 데이터셋을 요청하면 `403 forbidden`, 만료/폐기된 키는 `401 unauthorized`.

### 키 목록 / 폐기

```bash
# 목록 (secret은 보이지 않음)
curl $API/v1/admin/api-keys -H "Authorization: Bearer $ADMIN_KEY"

# 폐기 (disabled_at 세팅; 영구 삭제는 아님 — 감사 추적 용도)
curl -X DELETE $API/v1/admin/api-keys/<key_id> -H "Authorization: Bearer $ADMIN_KEY"
```

자기 자신을 폐기하려고 하면 `400` — 잠금 사고를 막기 위한 안전장치.

### 모니터링 API

| 메서드 + 경로                          | 용도                                                 |
| --------------------------------- | -------------------------------------------------- |
| `GET /v1/admin/stats`             | 상태별 잡 카운트, 활성 키 수, 24시간 잡, 누적 행                    |
| `GET /v1/admin/extracts`          | 추출 이력 목록. 쿼리: `api_key_id`, `status`, `dataset`, `limit`, `offset` |
| `GET /metrics`                    | Prometheus 형식 메트릭 (인증 없음 — 사설망에서만 노출 권장)            |

### 부트스트랩 키

로컬/dev 환경에서 `.env`의 `BOOTSTRAP_API_KEY`는 시작 시 자동으로
`is_admin=true`, `datasets=["*"]`, 무기한으로 시드/보장됩니다. 운영 환경에선
`.env`에서 이 값을 제거하고 첫 admin 키를 SQL로 직접 심는 방식이 안전합니다.

---

## 멱등성

같은 요청을 두 번 보내도 잡이 두 개 생기지 않게 하려면 `Idempotency-Key` 헤더를 함께 보내세요.

```bash
curl -X POST $API/v1/extracts \
  -H "Authorization: Bearer $KEY" \
  -H "Idempotency-Key: my-daily-export-2026-01" \
  -H "Content-Type: application/json" \
  -d '{...}'
```

같은 API 키 + 같은 Idempotency-Key는 항상 같은 잡을 반환합니다.

---

## 운영

| 항목                | 위치                                       |
| ----------------- | ---------------------------------------- |
| 헬스체크              | `GET /healthz`                           |
| Prometheus 메트릭    | `GET /metrics`                           |
| 컨테이너 로그           | `docker compose -f deploy/docker-compose.yml logs -f app worker` |
| 결과 파일 보존          | 기본 72시간 (`EXTRACT_RETENTION_HOURS`)      |
| 최대 행 수            | 기본 50,000,000 (`EXTRACT_MAX_ROWS`)       |
| 배치 크기             | 기본 10,000 (`EXTRACT_BATCH_SIZE`)         |
| 결과 파일             | `extract-data` Docker 볼륨 (`/var/lib/extracts`) |

스택 내리기:
```bash
docker compose -f deploy/docker-compose.yml down
# 데이터까지 같이 지우려면:
docker compose -f deploy/docker-compose.yml down -v
```

---

## 실제(원격) 소스 DB로 전환

`.env`에서 `SOURCE_*`만 바꾸면 됩니다. 코드 변경 없음.

```env
SOURCE_HOST=warehouse.singlestore.example.com
SOURCE_PORT=3306
SOURCE_USER=...
SOURCE_PASSWORD=...
SOURCE_DB=...
```

그리고 Compose의 `mysql` 서비스는 dev 전용이므로 운영에선 제거해도 됩니다.

---

## 트러블슈팅

**`status: failed`로 끝났고 `error_message`에 "exceeded max rows"**
→ 필터 범위가 너무 넓습니다. 기간을 좁히거나 `EXTRACT_MAX_ROWS` 상향.

**잡이 영영 `queued`에서 안 움직임**
→ 워커가 안 돌고 있을 확률.
`docker compose -f deploy/docker-compose.yml ps`로 worker 상태 확인.

**`422` "unknown filters"**
→ 화이트리스트에 없는 필드. 추가하고 싶다면 [registry.py](app/extract/registry.py)에 등록.

**다운로드가 404**
→ 보존 기간 초과 (`status=expired`)이거나 다른 API 키의 잡. 두 경우 다 의도된 거부.

---

## 구조와 설계 결정

자세한 설계는 [CLAUDE.md](CLAUDE.md)에 있습니다 (워커 흐름, keyset pagination 이유, 비-협상 규칙 등).
