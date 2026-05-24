# ASAP Extract — Admin Guide

운영자가 시스템을 띄우고, 키를 발급하고, 추출 상황을 감시하며, 장애 시 손볼 수 있도록 작성된 문서. 후반부에 **애플리케이션 아키텍처**가 포함됩니다 — 단순 운영을 넘어 시스템이 왜 이렇게 생겼는지 이해할 수 있도록.

---

## 1. 5분 띄우기

전체 스택은 Docker Compose로 단일 명령에 뜹니다.

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml up -d --build
```

뜨는 컨테이너:

| 서비스       | 역할                                                              |
| --------- | --------------------------------------------------------------- |
| `app`     | FastAPI ASGI 서버 (Uvicorn) + 마이그레이션/부트스트랩 초기화                     |
| `worker`  | Arq 비동기 워커 (실제 추출 수행)                                            |
| `postgres`| 서비스 메타 DB (jobs, api_keys)                                       |
| `redis`   | Arq 잡 큐 broker                                                  |
| `mysql`   | **개발용 소스 DB.** 운영에서는 원격 SingleStore로 대체. 시드: 100k events.        |
| `nginx`   | 리버스 프록시 + X-Accel-Redirect로 결과 파일 직접 서빙                          |

진입점:
- API: `http://localhost:8080`
- Admin 패널: `http://localhost:8080/admin`
- Swagger: `http://localhost:8080/docs`
- Prometheus 메트릭: `http://localhost:8080/metrics`

기동 직후 다음이 자동으로 처리됩니다:
1. `alembic upgrade head` — Postgres 메타 스키마 마이그레이션
2. `python -m app.bootstrap` — `.env`의 `BOOTSTRAP_API_KEY`를 admin 키로 시드 (또는 기존이면 admin/`["*"]`로 보장)

`docker compose ... logs app` 확인 시 "seeded admin api key" 또는 "ensured admin + *" 메시지가 보이면 정상.

---

## 2. 관리자 패널 (UI)

`http://localhost:8080/admin` → admin 키 입력 → 세 섹션이 보입니다.

### Overview (대시보드 타일)
- `Last 24h`, `Active keys`, `Total rows`, `In-flight cap / key`
- 상태별 잡 카운트 (`queued`, `running`, `succeeded`, `failed`, `cancelled`, `expired`)

### Issue API key (발급 폼)
- `Label` — 라벨 (필수, 식별용)
- `Datasets` — 쉼표 구분 (`events`) 또는 `*` (전체)
- `Expires (days)` — 만료 일수 (빈 칸이면 무기한)
- `Admin?` — 어드민 권한 여부

발급 직후 `full_key`가 한 번만 노출됩니다. 사용자에게 안전한 채널(1Password, 사내 메신저 비밀 메시지 등)로 전달.

### Recent extracts (이력 표)
- 컬럼: 시각 / 키 / dataset / **filters JSON** / status / rows / bytes / error / job_id
- 필터: API 키 / 상태 / dataset 으로 좁히기
- 잡 별로 사용자가 어떤 기간·카테고리·user_id를 요청했는지 한눈에

키는 페이지의 `sessionStorage`에만 저장되며 탭을 닫으면 사라집니다.

---

## 3. 관리자 엔드포인트 (CLI / 스크립트용)

패널 없이 API로 직접 다룰 때.

| 메서드 + 경로                            | 용도                                                          |
| ----------------------------------- | ----------------------------------------------------------- |
| `POST /v1/admin/api-keys`           | 새 API 키 발급 (response의 `full_key`는 한 번만 노출)                  |
| `GET /v1/admin/api-keys`            | 키 목록 (secret 미포함)                                           |
| `DELETE /v1/admin/api-keys/{key_id}` | 키 폐기 (`disabled_at` 세팅; 자기 자신은 400)                          |
| `GET /v1/admin/stats`               | 상태별 잡 카운트, 활성 키 수, 24h 잡, 누적 행, in-flight cap               |
| `GET /v1/admin/extracts`            | 추출 이력 (쿼리: `api_key_id`, `status`, `dataset`, `limit`, `offset`) |

모두 `is_admin=true` 키 필요. 일반 키는 `403`.

### 발급 요청 본문

```
POST /v1/admin/api-keys
Authorization: Bearer <admin_key>

{
  "label":           "data-team-jan",
  "datasets":        ["events"],
  "expires_in_days": 90,
  "is_admin":        false
}
```

응답 `full_key`는 **이 응답에만** 등장하며 이후엔 Argon2id 해시만 남습니다. 운영 정책: 발급 즉시 사용자에게 안전 채널로 전달하고, 응답은 로그에 남기지 마세요.

---

## 4. 권한 모델

키 한 장은 두 차원의 권한을 갖습니다.

| 차원         | 의미                                                  |
| ---------- | --------------------------------------------------- |
| `datasets` | 추출 가능한 데이터셋 화이트리스트. `["events"]`, `["*"]` 등.        |
| `is_admin` | true면 `/v1/admin/*` 호출 가능 (키 관리, 모니터링)              |

체크 위치:
- **인증** (`app.auth.keys.verify`): `disabled_at IS NULL` AND `(expires_at IS NULL OR expires_at > now)` AND Argon2 검증
- **추출 생성** (`app.api.v1.create_extract`): `key.allows_dataset(dataset)` — `*` 또는 명시적 화이트리스트
- **어드민 엔드포인트** (`app.api.deps.require_admin`): `is_admin`

키별 권한을 더 잘게 쪼개고 싶다면 `ApiKey` 모델에 컬럼 한두 개 추가 후 체크 한 줄. 지금은 의도적으로 최소화.

---

## 5. Rate limit — 키별 in-flight cap

같은 키가 큐를 폭주시키는 것을 막기 위해 **한 키가 동시에 `queued + running` 상태로 가질 수 있는 잡 수**에 상한 (기본 5, `.env`의 `EXTRACT_MAX_INFLIGHT_PER_KEY`).

상한 초과 시:
```
HTTP/1.1 429 Too Many Requests
Retry-After: 10
{"detail":"in-flight cap reached (5/5); wait for jobs to finish"}
```

잡이 끝나거나 cancel되면 카운터가 자연 회복.

**소프트 캡**입니다 — 동시 POST가 같은 순간에 카운트를 읽으면 일시적으로 cap+1까지 됩니다. RPS 단위의 강한 제한이 필요해지면 nginx `limit_req` 또는 별도 미들웨어로 직교적으로 추가.

---

## 6. 모니터링

### 패널 Overview
실시간 카운트. 첫 진입점.

### `GET /metrics` (Prometheus)
- `extracts_started_total{dataset}` — 카운터
- `extracts_finished_total{dataset, status}` — 카운터
- `extract_rows_total{dataset}` — 카운터 (총 추출 행)
- `extract_duration_seconds{dataset}` — 히스토그램

> `/metrics`는 인증 없음. 사설망에서만 노출하거나, 외부 노출이 필요하면 nginx에서 IP 제한.

### 구조화 로그
모든 로그는 stdout JSON. `request_id`로 HTTP ↔ 워커를 추적 가능:
```
docker compose -f deploy/docker-compose.yml logs -f app worker
```

각 잡은 `job_id` 컨텍스트 변수가 붙어 있어 grep 쉬움.

---

## 6.5. 디스크 레이아웃과 볼륨

```
<repo-root>/
  data/
    extracts/                    ← 호스트 bind mount, finder/탐색기에서 직접 접근
      <job_id>/
        result.csv               (성공 시)
        result.csv.part          (진행 중)
```

볼륨 구성:

| 볼륨 이름            | 종류         | 호스트 위치                       | 이유                              |
| --------------- | ---------- | ---------------------------- | ------------------------------- |
| 결과 파일            | bind mount | `./data/extracts/`           | 사용자가 직접 봐야 하는 파일. 백업/검수 편의.     |
| Postgres 데이터     | named volume | Docker가 관리 (호스트 직접 접근 X)    | 내부 바이너리. 호스트에 두면 UID 충돌·실수 위험. |
| MySQL (dev seed) 데이터 | named volume | Docker가 관리                | 동일                              |
| `app/static/`   | bind mount (ro) | repo의 `app/static/`         | admin.html 핫리로드.                |

named volume 위치 확인이 꼭 필요하면:
```bash
docker volume inspect extract-api_pg-data
docker volume inspect extract-api_mysql-data
```

### 다운로드 파일명 규칙

`Content-Disposition`이 제안하는 파일명:

```
<key_label>_<dataset>_<created_at_utc>_<shortid>.<ext>
```

예: `data-team-jan_events_20260125T134544Z_8d6f.csv`

구성 요소:

| 자리          | 내용                                                                     |
| ----------- | ---------------------------------------------------------------------- |
| `key_label` | 잡을 만든 API 키의 라벨. 영숫자·`.`·`-`·`_`만 남기고 lowercased, 32자로 절단. 빈 값이면 `unlabeled`. |
| `dataset`   | 추출 대상 데이터셋 이름 (`events` 등)                                              |
| `created_at_utc` | 요청 시각 UTC, 압축 ISO (`yyyymmddThhmmssZ`) — 텍스트 정렬해도 시간 순                |
| `shortid`   | job_id UUID의 첫 8자 — 같은 초의 충돌 회피, 필요 시 admin 패널/DB에서 역추적                |

데이터의 시간 범위(filter `from/to`)는 파일명에 포함하지 않습니다. 일부 dataset은 시간 필터가 없을 수 있고, 파일명이 길어집니다. 시간 범위는 admin 패널의 `filters` 컬럼이나 `GET /v1/extracts/{id}`에서 확인.

디스크 상의 실제 파일명은 항상 `result.csv` — 클라이언트에게 보내줄 때만 이 의미 있는 이름으로 wrap합니다.

---

## 7. 운영 작업

### 스택 라이프사이클

```bash
# 전체 기동
docker compose -f deploy/docker-compose.yml up -d --build

# 앱/워커만 재기동 (설정 변경 후)
docker compose -f deploy/docker-compose.yml up -d --build app worker

# 정지 (데이터 유지)
docker compose -f deploy/docker-compose.yml down

# 정지 + 데이터 삭제 (조심)
docker compose -f deploy/docker-compose.yml down -v
```

### 마이그레이션 추가

`alembic/versions/`에 새 리비전을 만들고, 컨테이너 재기동 시 `alembic upgrade head`가 자동 실행됩니다.

```bash
docker compose -f deploy/docker-compose.yml exec app \
  alembic revision --autogenerate -m "describe change"
```

운영에서는 마이그레이션이 자동 실행되므로 PR 단계에서 충분히 검토.

### 새 데이터셋 등록

코드 한 곳만 수정하면 됩니다.

`app/extract/registry.py`에 항목 추가:

```python
ORDERS = Dataset(
    name="orders",
    table="orders",
    columns=["id", "ordered_at", "customer_id", "amount", "status"],
    sort_columns=["ordered_at", "id"],   # 소스 DB에 복합 인덱스 필수
    required_filters=["from", "to"],
    optional_filters=["status", "customer_id"],
    list_filters={"status", "customer_id"},
)

REGISTRY = {
    EVENTS.name: EVENTS,
    ORDERS.name: ORDERS,        # ← 추가
}
```

원격 DB에 `(ordered_at, id)` 복합 인덱스가 반드시 있어야 합니다 — 없으면 keyset pagination이 풀스캔으로 떨어져 메모리/시간이 폭발합니다.

라우터/워커는 손댈 필요 없음.

### 운영 환경으로 전환

`.env`의 `SOURCE_*`만 실제 SingleStore 좌표로 바꿉니다.

```env
SOURCE_HOST=warehouse.singlestore.example.com
SOURCE_PORT=3306
SOURCE_USER=...
SOURCE_PASSWORD=...
SOURCE_DB=...
```

그리고:
- Compose의 `mysql` 서비스는 제거 또는 disable (dev 전용)
- `BOOTSTRAP_API_KEY`를 `.env`에서 제거하고 첫 admin 키를 직접 SQL로 심어두는 게 안전:
  ```sql
  -- run inside the postgres container
  INSERT INTO api_keys (key_id, secret_hash, label, datasets, is_admin)
  VALUES ('<8char>', '<argon2 hash>', 'root-admin', '["*"]'::jsonb, true);
  ```
- nginx는 외부 망에 노출되는 유일한 면이므로 TLS 종단 + `/metrics` 접근 제한 정책 점검

---

## 8. 트러블슈팅

| 증상                                       | 원인 / 조치                                                                      |
| ---------------------------------------- | --------------------------------------------------------------------------- |
| 모든 요청이 `502 Bad Gateway`                  | nginx 업스트림 IP 캐시 문제일 가능성. 우리 nginx.conf는 Docker DNS resolver를 사용하지만, 그래도 안 풀리면 `docker compose restart nginx`. |
| 잡이 영영 `queued`에서 안 움직임                   | `docker compose ps`로 worker 상태 확인. Redis 헬스도 함께.                              |
| `failed` + `error_class=ExtractTooLarge`  | 사용자 필터가 너무 넓음. 안내하거나 `EXTRACT_MAX_ROWS` 상향.                                   |
| 인증/만료된 키 호출이 자꾸 옴                         | 패널 → Recent extracts 필터로 해당 키의 패턴 확인 후 사용자에게 갱신 안내.                            |
| 디스크 부족                                    | `./data/extracts` 점검. 보존 기간(`EXTRACT_RETENTION_HOURS`) 단축 또는 청소 sweeper 도입.    |
| 다운로드가 404, 잡은 `succeeded`로 보임             | 옛 named volume(`extract-api_extract-data`)에 파일이 남아있는데 코드가 bind mount를 봄. 옛 볼륨에서 `./data/extracts/`로 디렉터리 복사하거나, 잡을 새로 만들어 받으세요. |

---

# 애플리케이션 아키텍처

운영자가 시스템의 형태를 이해하고 변경의 영향을 가늠할 수 있도록.

## 한 줄 요약

원격 SingleStore에서 대용량 데이터를 **메모리에 올리지 않고** 디스크 파일로 안전하게 뽑아내는 비동기 추출 API.

## 두 가지 비동기

같은 단어가 두 곳에서 다른 의미로 쓰입니다. 분리해서 이해해야 합니다.

1. **HTTP 레이어 (클라이언트 ↔ API)**
   `POST /v1/extracts` 즉시 반환 → 클라이언트가 폴링/webhook으로 완료 확인 → 다운로드. 클라이언트가 추출 동안 연결 유지하지 않음.

2. **워커 레이어 (워커 ↔ 소스 DB ↔ 디스크)**
   소스 DB에서 keyset pagination으로 청크 단위로 끊어 읽고, 청크 단위로 파일에 append. 메모리는 항상 한 청크 (기본 10000행) 크기만 유지. 디스크 파일이 점점 자라남.

두 비동기는 **독립적**이며 목적이 다릅니다. HTTP는 클라이언트를 자유롭게 하기 위해, 워커는 메모리를 안전하게 묶기 위해.

## 두 단계 데이터 플레인

```
  ┌─────────────────┐    read-only          ┌──────────────────────┐
  │   FastAPI app   │ ────────────────────► │  Remote SingleStore  │
  │   Arq worker    │   keyset pagination   │  (source data)       │
  └────────┬────────┘                       └──────────────────────┘
           │
           │ jobs, api_keys, audit
           ▼
  ┌─────────────────┐
  │   Postgres      │  (Compose service, owned by us)
  └─────────────────┘
```

**소스 DB와 메타 DB는 어떤 경우에도 같은 연결 풀·세션·트랜잭션을 공유하지 않습니다.** 신뢰성 가정이 완전히 다른 시스템(원격/lossy/read-only vs. 로컬/owned/transactional)이라 코드 차원에서 분리.

## 요청 → 결과 라이프사이클

```
POST /v1/extracts           ──► PG에 job row 작성 (status=queued) + Arq enqueue
                                즉시 {job_id} 반환
GET  /v1/extracts/{id}      ──► 상태 폴링
DELETE /v1/extracts/{id}    ──► cancel_requested = true → 워커가 다음 배치에서 종료
GET  /v1/extracts/{id}/download
                            ──► FastAPI가 인증만 처리 → X-Accel-Redirect로 Nginx에 위임
                                Python은 파일 바이트를 읽지 않음
```

## 워커 흐름

1. PG에서 `SELECT ... FOR UPDATE SKIP LOCKED`로 잡 점유 → `status=running`
2. `aiomysql`로 소스에 별도 연결 (풀 X — 추출은 long-running)
3. **Keyset pagination**: `WHERE (occurred_at, id) > (last_at, last_id) ORDER BY occurred_at, id LIMIT 10000`
4. 청크를 `<job_id>/result.csv.part`에 append. 메모리에는 한 청크만 유지.
5. 매 배치 사이에 `cancel_requested` 체크 — 사용자가 포기한 잡은 즉시 정리
6. 성공: `fsync` → atomic rename → `status=succeeded`, `row_count`/`bytes`/`sha256` 기록
7. 실패: `.part` 파일은 보존(포렌식), `status=failed` + `error_class`/`error_message` 기록

## 왜 `SSCursor`가 아니라 keyset인가

`aiomysql`의 `SSCursor`(server-side cursor)는 단일 노드 MySQL을 가정합니다. SingleStore는 분산 엔진이라 aggregator/leaf 경계를 넘는 서버 측 커서 동작이 보장되지 않습니다.

**Keyset pagination**의 장점:
- 워커는 마지막 본 키만 들고 있음 (서버 측 커서 X)
- 전송 끊김에 강함 — 재시도가 단순
- `LIMIT/OFFSET`은 분산 환경에서 비용 폭주 — 금지

## 왜 파일 + Nginx X-Accel-Redirect인가

대안: FastAPI가 응답으로 직접 chunked-stream.

**문제점:**
- 다운로드 시간 = 추출 시간 + 전송 시간 — 클라이언트가 그 시간 동안 연결 유지 필요
- 한 다운로드가 ASGI 워커 1개를 다 잡음 → 동시 다운로드 시 워커 starvation
- 네트워크 끊김 시 처음부터 다시
- 재다운로드 불가 (한 번 흘려보내면 끝)

**파일 + Nginx 방식:**
- 추출과 다운로드가 분리됨 — 사용자가 편한 시간에 다운로드
- Nginx는 sendfile 기반 zero-copy — Python 자원 무관
- HTTP Range 요청, 재다운로드 자유
- 다운로드 인증/로깅은 FastAPI에서, 바이트 전송은 Nginx에서

## 키 보관 모델

- `secret_hash`는 **Argon2id** 해시. 원본 secret은 어디에도 저장하지 않음
- 키 ID(8자 hex)로 빠른 인덱스 룩업 → 매칭된 행에 대해 Argon2 검증
- 폐기는 행 삭제가 아니라 `disabled_at` 타임스탬프 (감사 추적용 soft-delete)
- 만료 검증은 DB 쿼리 단계가 아닌 Python — 만료/폐기/잘못된 키 모두 동일하게 401 반환 (timing oracle 회피)

## 멱등성 보장

`POST /v1/extracts`에서 `(api_key_id, idempotency_key)` 유니크 제약. 동시 두 POST가 같은 키를 보내도 한쪽만 행을 만들 수 있고, 충돌한 쪽은 기존 잡을 그대로 반환. DB 차원에서 보장.

## 파일은 어디에 있나

`extract-data` Docker 볼륨 (`/var/lib/extracts`). 컨테이너 내부에서:

```
/var/lib/extracts/
  └─ <job_id>/
       └─ result.csv          (또는 result.csv.part 진행 중)
```

볼륨은 app/worker/nginx 세 컨테이너가 공유 마운트. 사용자는 Nginx의 internal location `/_internal/extracts/`를 통해서만 접근 가능 (FastAPI가 `X-Accel-Redirect` 헤더로 위임).

## 코드 한눈에

| 위치                                  | 책임                                                       |
| ----------------------------------- | -------------------------------------------------------- |
| `app/api/v1.py`                     | 추출 라우터 (POST/GET/DOWNLOAD/DELETE). 얇음.                   |
| `app/api/admin.py`                  | 어드민 라우터 (키 발급/조회/폐기, stats, extracts 목록)                  |
| `app/api/deps.py`                   | HTTPBearer + require_api_key + require_admin             |
| `app/auth/keys.py`                  | 키 발급/검증 (Argon2id)                                       |
| `app/extract/registry.py`           | **추출 가능 데이터셋의 단일 진실원.** 새 데이터셋 추가는 여기 한 곳만.               |
| `app/extract/paginator.py`          | Keyset 쿼리 빌더. 입력 안전(컬럼명은 Dataset 정의에서만 옴 → SQL injection 불가) |
| `app/extract/writer.py`             | 한 배치씩 CSV로 쓰는 stateless writer                            |
| `app/extract/pipeline.py`           | 라우터·워커가 공유하는 파이프라인 단일 함수. 모든 레이어가 여기서 만남.                  |
| `app/workers/arq_worker.py`         | Arq task. `pipeline.run(job_id)` 한 줄 호출.                  |
| `app/storage/paths.py`              | 디스크 레이아웃, atomic rename, X-Accel 경로 매핑                    |
| `app/db/meta/`                      | Postgres 모델 + 엔진                                         |
| `app/db/source/connection.py`       | aiomysql 연결 헬퍼 (풀 X)                                     |
| `app/observability/`                | structlog 설정, request_id 미들웨어, Prometheus 메트릭             |
| `app/static/admin.html`             | 단일 파일 admin 패널 (vanilla JS)                              |
| `alembic/versions/`                 | DB 마이그레이션                                                |
| `deploy/`                           | Dockerfile, docker-compose.yml, nginx.conf, mysql seed   |

> 보다 깊은 설계 결정과 비-협상 규칙은 [../CLAUDE.md](../CLAUDE.md)에 정리되어 있습니다.
