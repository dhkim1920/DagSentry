# DagSentry 명세 구현·검증 기록

기준: 2026-09-11 사용자 제공 T00–T16 명세. 과거 구현 기록을 완료 근거로 사용하지 않는다.
현재 파일 존재와 실행 검증을 구분한다. 아래 상태는 초기 조사 결과이며 전체 완료가 아니다.

## 작업 현황

| 작업 | 구현 상태 | 근거 파일 및 남은 검증·구현 |
| --- | --- | --- |
| T00 | 부분 구현 | 본 문서, `pyproject.toml`, `migrations/versions/`; 상세 항목별 감사·조직 정보 검사는 계속 필요 |
| T01 | 기존 구현 / 부분 검증 | `models.py`, `ingestion.py`, `routes/failures.py`, `tests/test_ingestion.py`; PostgreSQL 통합 21건 실행, 필수 항목별 추가 감사 필요 |
| T02 | 부분 구현 | `airflow/`, `tests/test_reconciler.py`; 페이지 수정, lazy 자동 등록, 3.1.8/3.3.1 호환 각 9건 통과, 실행 중 데이터 변경 검증 필요 |
| T03 | 기존 구현 / 검증 대기 | `worker.py`, `pipeline.py`, `task_logs.py`, `log_processing.py`, `worker_admin.py`; PostgreSQL 다중 Worker 검증 대기 |
| T04 | 부분 구현 | `rule_diagnosis.py`, `error_signature.py`, `diagnosis.py`; 한국어 조치·재시도 판단과 ruleset v2 연결 완료 |
| T05 | 부분 구현 | `ai_diagnosis.py`, `evidence_validation.py`, `providers/`; 다섯 Provider 공통 한국어 프롬프트 적용, 실제 호출 미실행 |
| T06 | 기존 구현 / 검증 대기 | `incident.py`, `recovery.py`; PostgreSQL 활성 유일성·실제 복구 검증 대기 |
| T07 | 부분 구현 | `notification.py`, `providers/`, `incident.py`; 순차 폴백·최종 실패 추가 1회·0019·한국어/KST 구현, 실제 렌더링 미검증 |
| T08 | 부분 구현 | v2 로컬 하루·상위 실패·0020·Anthropic/Bedrock 요약 구현 및 회귀 검증, 외부 모델/렌더링 실검증 미완료 |
| T09 | 기존 구현 / 검증 대기 | `identity.py`, `connection_*.py`, `routes/`, `web/`; 신규 실패 상태·리포트 연결 및 화면 검증 필요 |
| T10 | 부분 구현 | `metrics.py`, `observability.py`; retention 모듈과 CLI 미구현 |
| T11 | 부분 구현 | `Dockerfile`, `compose.production.yaml`; 외부 DB·TLS 오버레이 미구현, healthcheck entrypoint 보완 필요 |
| T12 | 미구현 | setup·compose 래퍼와 `deployment/systemd/` 없음 |
| T13 | 부분 구현 | `docs/backup-recovery.md`; 자동 백업 스크립트·실제 격리 복원 미완료 |
| T14 | 부분 구현 | `.github/workflows/ci.yml`; Airflow 3.1.8/3.3.1 고정 및 no-sync 적용, GitLab CI 없음 |
| T15 | 부분 구현 | 기존 정적 검사·로컬 테스트 실행 완료; PostgreSQL·Compose·화면·전체 문서 검증 대기 |
| T16 | 검증 대기 | 이번 작업에서 외부 호출·배포 미실행; 승인된 테스트 환경 필요 |

경로는 특별한 표기가 없으면 `src/dagsentry/` 기준이다. 기존 다섯 LLM Provider
(OpenAI, Azure OpenAI, Anthropic, Bedrock, Ollama)와 다섯 알림 Provider
(Webhook, Slack, Teams, Discord, SMTP)를 유지한다.

## T00 계약 조사

- 실행 진입점: `pyproject.toml`의 API, Worker, worker-jobs, recovery-checker,
  daily-report, scheduler, scheduler-health, admin. retention은 아직 없다.
- 흐름: `routes/failures.py` → `ingestion.py` → Failure Event/Outbox → `worker.py`
  → `pipeline.py` → 로그·진단·Incident·알림. Recovery와 Scheduler는 별도 진입점이다.
- API: `api.py`가 auth, admin, admin_connections, failures, incidents,
  human_diagnoses, error_signatures, diagnoses, daily_reports, daily_report_schedules를 등록한다.
- 모델/마이그레이션: Failure Event, Outbox, Signature, Diagnosis, Notification,
  Incident/전이/복구전달, 지표, Report, 사용자/세션/감사, Connection,
  운영자 진단, 스케줄/실행/heartbeat. 소스 Alembic head는 `0018`이다.
  운영 DB의 적용 리비전을 조회하거나 변경하지 않았다.
- event_key v1: `environment`, `dag_id`, `dag_run_id`, `task_id`, `map_index`,
  `try_number`, `event_key_version=1`의 키 정렬·공백 없는 UTF-8 JSON SHA-256.
  식별자는 NFC 정규화한다. source/state/observed_at은 키에 포함하지 않는다.
- fingerprint v1: `operator_type`, `exception_class`, `vendor_error_code`,
  `normalized_message`, `application_stack_frame`의 명시적 null을 포함한 canonical JSON
  SHA-256. fingerprint 버전은 별도 저장한다. 예외·vendor code·message가 모두 없으면 UNSIGNABLE.
- 전달 키: `notification.py`는 diagnosis_id와 delivery_key_version,
  `daily_report.py`는 report_date/environment/version/type,
  `recovery.py`는 incident_id/version/type의 canonical JSON SHA-256을 사용한다.
- 유효 진단: `pipeline.py`에서 해당 이벤트의 PASSED 중 created_at DESC, id DESC.
  재사용은 같은 signature/fingerprint 버전, PASSED, RULE 또는 AI 원본,
  schema/prompt/rule 버전 일치, 기본 30일 이내(경계 포함). Rule의 prompt 불일치로
  AI 정책에서 재사용되지 않는 기존 동작을 보존한다.
- Incident 활성 식별자는 environment/dag_id/task_id/error_signature_id.
  OPEN → ACKNOWLEDGED/RECOVERED/RESOLVED/IGNORED,
  ACKNOWLEDGED → RECOVERED/RESOLVED/IGNORED,
  RECOVERED → RESOLVED/IGNORED. 같은 상태는 no-op.
  종료 상태 override는 기존 명시적 운영자 옵션을 따른다. AI 상태 변경은 허용하지 않는다.
- 실패 횟수: `reporting.py`의 failure_attempts는 기간 안의 Failure Event 행 수.
  distinct TaskInstance와 DAG run 집계는 별도다. 현재 기간은 UTC 하루다.
- 의존성: `uv.lock` 존재. 현재 extra 하한은 Airflow 3.3.1이며 명세의 3.1.8과 다르다.
  기존 `verify-v01.sh`는 DB URL을 환경에서 받아 migrate하므로 이번에는 실행하지 않았다.
  설치 버전을 바꾸지 않도록 현재 `.venv/bin/` 실행 파일로 검사했다.
- 결정 필요: 추가 규칙·민감정보 정책·모델 합격률·인증서 만료 감지 담당은 사용자 명세대로
  보류한다. SSO/OIDC/Kubernetes/LLM 자동 폴백/추가 재알림/서버 자동 배포도 제외한다.

## 시작 시점 검증

작업 ID: T00

상태: 부분 완료

변경 파일: 본 문서

실행 명령 및 실제 결과:

```text
.venv/bin/alembic heads                 → 0018 (head)
.venv/bin/ruff check .                  → All checks passed
.venv/bin/ruff format --check .         → 260 files already formatted
.venv/bin/mypy                         → 159 source files, no issues
.venv/bin/pytest tests -q --ignore=tests/integration
                                      → 599 passed in 10.27s (Airflow 포함)
.venv/bin/pytest tests/airflow -q        → 8 passed in 0.51s (위 599건의 부분집합)
```

실제 설치 버전: Python 3.11.15, Airflow 3.3.1, pytest 9.1.1, ruff 0.16.2,
mypy 1.20.2. 시작 worktree 변경 없음. 시작 시점 실패 없음.

실연동 여부: Mock 및 격리 SQLite. 실제 Airflow 패키지 호환 테스트이며 서버 E2E는 아니다.

남은 제약: PostgreSQL 통합 테스트, Python 3.12/Airflow 3.1.8, Compose,
systemd, 실제 알림·LLM·인증서·복원 검증 미실행.

## T02 페이지 누락 수정

상태: 부분 완료

변경 파일: `src/dagsentry/airflow/reconciler.py`, `tests/test_reconciler.py`,
`docs/reconciler.md`, `docs/reconciler.ko.md`

구현 또는 수정 내용: offset=0 시작, 응답의 cursor 필드 존재와 null 구분,
실수신 행 수 기반 offset 증가, 유효 total/빈 페이지 종료, 10,000페이지 제한.
커서 응답에서 이후 필드가 사라지면 실패하여 watermark를 보존한다.

실행 명령: `.venv/bin/pytest tests/test_reconciler.py -q`

실제 결과: 수정 전 선택한 회귀 테스트 6건 실패로 재현. 수정 후 11 passed in 2.91s.
요청 500건/응답 100건, 잘못된 total 값, offset/cursor 양쪽 페이지 상한,
상한 실패 시 watermark 보존, 기존 재전송 멱등성 검증 포함.

실연동 여부: HTTP Mock 및 SQLite

남은 제약: 3.1.8 실제 패키지/API, lazy 등록, 실행 중 데이터 변경 E2E 미검증.

수정 후 전체 검증: `.venv/bin/ruff check .` 통과,
`.venv/bin/ruff format --check .` 261개 파일 통과,
`.venv/bin/mypy` 159개 소스 통과,
`.venv/bin/pytest tests -q --ignore=tests/integration` 606 passed in 12.90s,
`git diff --check` 통과. 중간 mypy의 params 타입 불일치는 기존 `_get_json` 계약에
맞춰 수정했다. README와 한국어 색인에 작업 기록 링크를 추가했다.

다음 작업: T01 PostgreSQL 격리 검증 → T02 나머지 호환성 → T03–T14 누락 구현
→ T15 회귀 검증. T16은 승인된 외부 테스트 환경에서 별도 수행한다.

## 두 번째 작업: T01/T02/T04/T05/T07/T10 진전

상태: 부분 완료. 전체 명세 목표는 유지한다.

변경 파일:

- T02: `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `tests/airflow/`,
  README와 영문·한국어 Airflow/버전 호환 문서.
- T04: `rule_diagnosis.py`, `diagnosis.py`, 해당 테스트, 영문·한국어 Rule 문서.
- T05: `prompts.py`, `config.py`, OpenAI/Anthropic/Bedrock/Ollama Provider
  (Azure는 OpenAI 경로 상속), `tests/contracts/llm.py`, 설정 예시와 AI 문서.
- T07: `providers/fallback.py`, `providers/notification_adapter.py`, 팩토리,
  `daily_report.py`, `recovery.py`, `tests/test_fallback_provider.py`, Provider 계약 문서.
- T10: `worker.py`, `tests/test_worker.py`, 영문·한국어 Worker 복구 문서.

구현 또는 수정 내용:

- 별도 PostgreSQL 17 컨테이너를 생성하고 빈 DB에 0001–0018 마이그레이션 후
  통합 테스트 21건을 실행했다. 동시 Ingest/Worker claim, stale 회수, DB 연결 복구,
  Incident, 진단, 전달, 마이그레이션, 역할 권한 등 기존 테스트 범위다.
- Airflow extra 하한 3.1.8, plugin 엔트리포인트, 기본 lazy Provider/Listener 발견 테스트.
  CI는 정확한 3.1.8/3.3.1 설치 후 `uv run --no-sync`와 실제 버전 단언을 사용한다.
  DagBag import 차이는 테스트에서만 처리했다.
- Rule v2 한국어 권장 조치/재시도 판단을 저장·알림 경로에 연결했다.
  root_cause는 생성하지 않는다. 기존 정규식·우선순위·fingerprint는 유지했다.
- AI 공통 프롬프트가 한국어 원인·조치와 원문 근거를 함께 요청한다.
  다섯 Provider 계약 테스트에서 실제 요청 본문에 공통 지침이 들어가는지 검사한다.
- 순차 폴백, 첫 성공 종료, primary 오류 유지, 체인당 기록 한 행과 동일 전달 키,
  기존 Teams Report/Recovery 재개를 검증했다. 기존 DB Slack 연결 범위는 유지한다.
- Worker 예외 메시지의 Outbox 저장과 DB traceback 출력을 제거하고 고정 문구·타입을
  사용한다. WARNING 재시도와 ERROR DEAD 로그에 Secret이 없는지 검사했다.

실행 명령 및 실제 결과:

```text
docker run -d --rm --name dagsentry-spec-test-postgres
  -e POSTGRES_USER=spec_test -e POSTGRES_PASSWORD=<disposable-test-value>
  -e POSTGRES_DB=spec_test -p 127.0.0.1:25432:5432 postgres:17-alpine
env DAGSENTRY_DATABASE_URL=<isolated-test-url> .venv/bin/alembic upgrade head
  → 0001–0018 적용 성공
env DAGSENTRY_DATABASE_URL=<isolated-test-url> DAGSENTRY_TEST_DATABASE_URL=<same-url>
  .venv/bin/pytest tests/integration -q --ignore=tests/integration/test_ollama_runtime.py
  → 21 passed in 2.83s; 변경 후 재검증 21 passed in 2.88s
env UV_CACHE_DIR=/tmp/dagsentry-uv-cache uv lock
env UV_CACHE_DIR=/tmp/dagsentry-uv-cache uv sync --locked --extra airflow
  → lock 갱신 및 DagSentry 패키지 재설치, Airflow 3.3.1 유지
.venv/bin/pytest tests/airflow -q
  → 9 passed in 1.16s (Python 3.11.15 / Airflow 3.3.1)
/private/tmp/dagsentry-airflow318.oTLDyw/venv/bin/pytest tests/airflow -q
  → 9 passed, 33 upstream locale/deprecation warnings in 3.72s
  → 별도 venv, 실제 설치 apache-airflow==3.1.8 단언 후 실행
.venv/bin/pytest tests/test_fallback_provider.py -q
  → 9 passed in 0.16s
.venv/bin/ruff check . → 통과
.venv/bin/ruff format --check . → 265개 파일 통과
.venv/bin/mypy → 163개 소스 통과
.venv/bin/pytest tests -q --ignore=tests/integration
  → 618 passed in 13.42s (Airflow 3.3.1 포함, skip 없음)
git diff --check → 통과
```

실연동 여부: PostgreSQL 격리 테스트 DB는 실제 실행. LLM/알림은 Mock, Airflow는 실제 패키지
호환 검사이며 서버 실패 E2E는 미실행. 운영 DB·외부 알림·배포는 실행하지 않았다.

남은 제약: T00 필수 항목별 정밀 감사, PostgreSQL 중간 트랜잭션 실패 추가 검증,
실제 실패 E2E, Python 3.12 실행, 최종 실패 0019, 리포트 v2/0020,
retention, TLS/외부 DB Compose, setup/timer/backup/GitLab CI 및 T16.
운영 예시의 Teams/SMTP 기본값은 Secret 소비 경로와 Compose 스모크를 함께 맞출 때 변경한다.
검증 후 `docker stop dagsentry-spec-test-postgres`로 폐기용 컨테이너를 정리했다.
`--rm` 컨테이너의 테스트 DB도 폐기되었으며 다음 검증 때 빈 DB로 다시 생성한다.

다음 작업: T07 최종 FAILED 선점·추가 1회 알림과 0019 → T08 리포트 v2 및 0020
→ T10 retention → T11–T14 운영 구성 → T15 전체 검증 → 승인된 환경의 T16.

## 세 번째 작업: T07 최종 실패 알림 및 표시

작업 ID: T07 / T09 / T15

상태: 부분 완료 (외부 렌더링·브라우저 검증 미실행)

변경 파일: `migrations/versions/0019_final_failure_notification.py`, `models.py`,
`incident.py`, `domain/notification.py`, `pipeline.py`, `display.py`, `config.py`,
Teams/SMTP/Slack/Discord/Webhook Provider, `routes/incidents.py`, `web/app.js`,
관련 단위·PostgreSQL 통합 테스트, 설정 예시, 영문·한국어 Incident/알림 문서.

구현 또는 수정 내용:

- 기존 0018을 변경하지 않고 0019에 nullable final_failure_event_id와 FK를 추가했다.
  최초 이벤트가 이미 FAILED인 기존 Incident는 해당 이벤트로 backfill한다.
- 상관 처리 트랜잭션 안에서 active 상태와 미선점 조건의 UPDATE로 최종 이벤트를 선점한다.
  첫 이벤트가 FAILED면 최초·최종 대상이 동일하여 1회만 보낸다. retry로 시작하면 최종 FAILED를
  추가 1회 보내고, 이후 FAILED는 REPEATED_FINAL_FAILURE로 억제한다.
- 실패한 전송을 재시도하면 선점과 전달 키를 보존하고 유효 진단·LLM 호출을 반복하지 않는다.
- Teams/SMTP에 한국어 라벨, 실패 상태, KST 시각·근거·권장 조치를 표시한다.
  DAGSENTRY_DISPLAY_TIMEZONE은 IANA 문자열이며 기본 Asia/Seoul이다.
  Slack/Discord도 실패 상태를 표시한다. Rule의 원인 설명 없음과 권장 조치를 구분한다.
- 새 failure_state는 저장 payload와 메시지 Provider에 연결하지만 Webhook에서는 제외하여
  기존 기계 계약을 보존한다. Webhook 계약 테스트가 새 필드의 비노출과 기존 payload 일치를 단언한다.
- Incident 상세 failure에 is_initial_failure/is_final_failure를 추가하고 한·영 화면 표시를 연결했다.
  최종 표시 대상은 선점 이벤트이며 외부 전달 성공 자체를 뜻하지 않는다.

실행 명령 및 실제 결과:

```text
.venv/bin/pytest tests/test_pipeline.py tests/test_teams_provider.py
  tests/test_smtp_provider.py tests/test_slack_provider.py tests/test_discord_provider.py -q
  → 초기 정책 변경 검증 77 passed
env DAGSENTRY_DATABASE_URL=<isolated-test-url> .venv/bin/alembic upgrade head
  → 격리 PostgreSQL 17, 0001–0019 성공
env DAGSENTRY_TEST_DATABASE_URL=<isolated-test-url> .venv/bin/pytest
  tests/integration -q --ignore=tests/integration/test_ollama_runtime.py
  → 22 passed in 3.08s
env DAGSENTRY_DATABASE_URL=<isolated-test-url> .venv/bin/alembic downgrade 0018
env DAGSENTRY_DATABASE_URL=<isolated-test-url> .venv/bin/alembic upgrade head
  → 0019 → 0018 → 0019 성공 (운영 롤백 절차로 권장하지 않음)
.venv/bin/ruff check . → 통과
.venv/bin/ruff format --check . → 268개 파일 통과
.venv/bin/mypy → 165개 소스 통과
.venv/bin/pytest tests -q --ignore=tests/integration → 623 passed in 13.59s
git diff --check → 통과
```

PostgreSQL 동시성 테스트는 최초부터 FAILED인 경로와 retry Incident에 8개 FAILED가 동시에
연결되는 경로를 각각 검사한다. 승자 1개, 같은 승자의 재처리 시 선점 유지, 활성 Incident 1개를
단언한다. 이전 리비전 데이터의 final_failure_event_id backfill도 실제 PostgreSQL에서 검사했다.
중간 로컬 포트 연결이 sandbox에서 거부된 실행은 실패로 기록하고 승인된 재실행에서 통과했다.

실연동 여부: 실제 격리 PostgreSQL, 알림 HTTP Mock/SMTP fake. 운영 DB/외부 알림/배포 미실행.

남은 제약: 실제 Teams·SMTP·브라우저 확인, T08 리포트 v2/0020 및 이후 운영 구현은 미완료.

다음 작업: T08 시간대별 전일 집계, top_failures, 한국어 리포트, Anthropic/Bedrock 요약,
0019 이후 0020 이관 → T10 retention → T11–T14 운영 구성 → T15/T16.

## 네 번째 작업: T08 리포트 v2 기간·집계·이관

작업 ID: T08 / T09 / T15

상태: 부분 완료

변경 파일: `domain/reporting.py`, `reporting.py`, `daily_report.py`, `report_app.py`,
`scheduled_reporting.py`, `config.py`, `display.py`, Teams/SMTP Provider,
`routes/daily_report_schedules.py`, `web/`, `migrations/versions/0020_daily_report_v2.py`,
관련 report 테스트와 영문·한국어 리포트/통계 문서.

구현 또는 수정 내용:

- Statistics/Report v2, IANA timezone, 로컬 자정의 UTC 변환. CLI/서비스 기본 Asia/Seoul,
  신규 UI 스케줄 기본 09:00 Asia/Seoul. 기존 저장 스케줄은 변경하지 않는다.
  직접 aggregate 함수는 기존 UTC 기본값을 유지하며 서비스가 선택한 시간대를 명시적으로 전달한다.
- Scheduler 실행 날짜와 수동 실행의 완료 날짜 검사를 스케줄 시간대에 맞췄다.
  매 실행에 기존 session_factory를 전달하여 새 엔진 생성을 피한다.
- DAG/Task별 이벤트 수 내림차순 상위 20개. 최신 진단된 실패의 분류/Incident/원인과
  실제 마지막 실패 시각을 분리하며 REUSED 원본 내용을 해석한다.
- 한국어 Rule 리포트와 전용 상위 실패 영역을 Teams/SMTP/UI에 연결했다.
  highlights에 목록을 중복 추가하지 않는다. 이관된 빈 목록과 실패 없음을 구분한다.
- 0020은 v1 통계의 UTC 의미·날짜·기간을 유지하고 schema v2와 빈 top_failures를 추가한다.
  비UTC v2 데이터가 있으면 모든 변경 전에 downgrade를 거부한다. 기존 리포트는 재계산하지 않는다.
- 잘못된 CLI timezone은 DB 엔진/Provider 생성 전에 거부한다.

실행 명령 및 실제 결과:

```text
.venv/bin/pytest tests/test_report_migration.py tests/test_reporting.py tests/test_daily_report_scheduler.py -q
  → 12 passed in 0.25s
.venv/bin/pytest tests -q --ignore=tests/integration
  → 626 passed in 13.61s
.venv/bin/pytest tests/test_report_app.py -q
  → 추가 CLI 회귀 1 passed in 0.09s (앞선 626건 실행 이후 추가)
.venv/bin/ruff check . → 통과
.venv/bin/ruff format --check . → 271개 파일 통과
.venv/bin/mypy → 167개 소스 통과
env DAGSENTRY_DATABASE_URL=<isolated-test-url> .venv/bin/alembic upgrade head
  → 별도 PostgreSQL 17 빈 DB, 0001–0020 성공
env DAGSENTRY_TEST_DATABASE_URL=<isolated-test-url> .venv/bin/pytest tests/integration -q --ignore=tests/integration/test_ollama_runtime.py
  → 22 passed in 3.14s
git diff --check → 통과
```

실연동 여부: PostgreSQL 실제 격리 DB, 메시지 Mock. 0020 기존 JSON 변환·비UTC downgrade 거부는
SQLite migration context에서 검사했다. PostgreSQL에서는 빈 DB 마이그레이션과 기존 통합 범위를
검사했으며 기존 v1 리포트 데이터의 실제 PostgreSQL 변환은 추가 검증 대상이다.

남은 제약: Anthropic/Bedrock 리포트 요약, daily-report-ko-v1 공통화, PostgreSQL v1 데이터 이관,
상위 20개 제한/REUSED/중복 렌더링 추가 검사, 브라우저 시각 검증. T10–T16 전체 목표는 계속 미완료.

다음 작업: T08 남은 Provider·회귀 검증 → T10 retention → T11–T14 배포·설정·백업·CI.

### T08 추가 검증 및 커밋 준비

사용자가 현재 작업을 마무리한 뒤 커밋·푸시하도록 요청했다. 원격 main과 HEAD는 fetch 후
ahead/behind 0/0으로 확인했다. 전체 명세 완료를 뜻하지 않으며 현재까지의 기능 변경 단위를 기록한다.

추가 변경: `providers/anthropic_report.py`, `providers/bedrock_report.py`,
`providers/report_summary.py`, OpenAI report adapter, `prompts.py`, Provider 팩토리,
`report_app.py`, 설정 예시. daily-report-ko-v1 공통 한국어 지침과 입력 직렬화,
HTTP 재시도 재사용 및 Bedrock 고유 SDK 예외 처리를 구현했다. SDK 클라이언트는 기존
total_max_attempts=1 경로를 사용하여 애플리케이션 재시도와 중복하지 않는다.

추가 검증:

- Anthropic/Bedrock HTTP Mock·SDK stub: 정상 한국어 응답, JSON/스키마 오류,
  영구 오류, 일시 오류 후 성공, 재시도 소진. 실모델 호출은 하지 않았다.
- 실제 격리 PostgreSQL 별도 schema에서 v1 JSON 변환과 비UTC downgrade 거부·데이터 보존 검증.
- 상위 20개 제한과 동률 정렬, CLI 잘못된 시간대의 엔진/Provider 생성 전 거부 검증.
- 최종 `.venv/bin/pytest tests -q --ignore=tests/integration`: 640 passed in 13.59s.
- 최종 격리 PostgreSQL 통합: 23 passed in 3.35s (Ollama 실연동 테스트 명시적 제외).
- ruff check/format(276개 파일), mypy(172개 소스) 통과.

남은 제약: Provider timeout·수신 렌더링·브라우저의 더 넓은 시나리오 및 T10–T16은 계속 진행한다.
T08 현재 구현을 커밋하되 전체 명세나 실제 외부 연동 완료로 표시하지 않는다.
