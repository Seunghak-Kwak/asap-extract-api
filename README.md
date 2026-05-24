# ASAP Extract API

원격 데이터 웨어하우스에서 **대용량 데이터를 메모리에 올리지 않고** 안전하게 추출하는 비동기 API.
한 추출이 수백만 행에 달해도 메모리는 한 청크 크기만 유지하며, 결과는 디스크에 파일로 떨어진 뒤 Nginx로 직접 전달됩니다.

## 핵심 흐름

```
1)  추출 요청      →  서버가 job_id 발급 (즉시 응답)
2)  진행 폴링      →  워커가 백그라운드에서 키셋 페이지네이션으로 청크 단위 추출
3)  결과 다운로드   →  완료된 파일을 Nginx가 zero-copy로 전송
```

요청과 추출은 분리되어 있어, 네트워크가 끊겨도 잡은 그대로 진행되고 사용자는 편한 시간에 결과를 받아갑니다.

## 문서

| 대상                              | 문서                                               |
| ------------------------------- | ------------------------------------------------ |
| API를 사용하는 사람                    | [docs/user-guide.md](docs/user-guide.md)         |
| 시스템을 띄우고 관리하는 사람 (+ 아키텍처)        | [docs/admin-guide.md](docs/admin-guide.md)       |
| 코드를 만지는 사람 (설계 결정 + 비-협상 규칙)     | [CLAUDE.md](CLAUDE.md)                           |

## 진입점

| 항목                  | URL (로컬)                              |
| ------------------- | ------------------------------------ |
| API                 | `http://localhost:8080`              |
| Admin 패널            | `http://localhost:8080/admin`        |
| Swagger UI          | `http://localhost:8080/docs`         |
| Prometheus 메트릭      | `http://localhost:8080/metrics`      |

운영 환경에서는 `localhost:8080` 부분이 실제 호스트로 바뀝니다.

## 스택

FastAPI · Arq · PostgreSQL · Redis · Nginx · 원격 SingleStore (개발 환경은 MySQL 8 컨테이너로 대체).
세부 구성과 이유는 [Admin Guide의 아키텍처 섹션](docs/admin-guide.md#애플리케이션-아키텍처).
