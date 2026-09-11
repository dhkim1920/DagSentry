# 일일 리포트 v2

DagSentry는 로컬 날짜와 환경별로 하루에 하나의 리포트를 생성합니다. 리포트에는 버전이 지정된
`DailyStatistics` 스냅샷과 완전한 규칙 기반 섹션이 항상 포함됩니다. AI는 선택 사항이며 서술형
내용만 추가할 수 있습니다.

## 생성 경계

생성 흐름은 고정되어 있습니다.

```text
PostgreSQL SQL 집계
→ 불변 DailyStatistics
→ 결정적 RuleBasedDailyReport
→ 선택적 DailyReportAISummary
→ 멱등적으로 전송되는 알림 payload
```

`DailyReportAISummary`에는 `key_changes`와 `priorities`만 들어갑니다. 엄격한 스키마는
`statistics` 필드를 허용하지 않으며, 전송하는 숫자 섹션은 AI 응답이 아니라 저장된 불변 Statistics
스냅샷에서 재구성됩니다. AI 응답이 잘못되었거나 사용할 수 없거나 스키마를 위반하면 해당 응답은
버리고 규칙 기반 리포트는 계속 저장·전송합니다.

`daily_reports` 테이블은 `(report_date, environment)` 유니크 제약을 가집니다. Statistics, 규칙
리포트, 선택적 AI 문장, 안정적인 delivery key, 전송 시도 상태를 저장합니다. 성공한 리포트를 다시
실행해도 두 번째 전송은 발생하지 않습니다. 전송에 실패한 경우에는 Statistics를 다시 계산하거나 AI를
다시 호출하지 않고 저장된 payload를 재시도합니다.

## 설정과 수동 실행

마이그레이션 `0020`을 적용하고 최소한 다음을 설정합니다.

```text
DAGSENTRY_DATABASE_URL=postgresql+psycopg://...
DAGSENTRY_ENVIRONMENT=production
DAGSENTRY_NOTIFICATION_PROVIDER=smtp
```

SMTP를 쓸 경우에는 [SMTP 이메일 알림 Provider](smtp-provider.ko.md)의 추가 설정도 필요합니다.
다른 알림 Provider를 사용한다면 그 Provider에 필요한 설정을 대신 적용하세요.

기본적으로 전날 Asia/Seoul 리포트를 생성하며 날짜와 `--timezone UTC`를 지정할 수 있습니다.

```shell
uv run dagsentry-daily-report
uv run dagsentry-daily-report --date 2026-08-12
```

규칙 기반 리포트만 사용하려면 `DAGSENTRY_LLM_PROVIDER`를 설정하지 않습니다. OpenAI 서술을
추가하려면 `DAGSENTRY_LLM_PROVIDER=openai`, `DAGSENTRY_LLM_MODEL`,
`DAGSENTRY_OPENAI_API_KEY`를 설정하고 필요하면 `DAGSENTRY_DAILY_REPORT_PROMPT_VERSION`도
설정합니다.

Anthropic·Bedrock도 선택한 진단 Provider의 연결·모델 설정으로 리포트 요약을 생성합니다.
세 경로는 daily-report-ko-v1 프롬프트와 통계/Rule 입력 직렬화를 공유하며 실제 응답을 검증합니다.
HTTP 재시도는 공통화하고 Bedrock은 SDK 오류 분류와 중복 SDK 재시도 방지를 유지합니다.
LLM 자동 폴백 체인은 없으며 호출 실패 시 Rule 리포트를 유지합니다.

## APScheduler 자동 실행

`dagsentry-scheduler`를 별도 장기 실행 프로세스로 배포합니다. Daily Reports UI에서 환경별 실행 시각과
timezone을 설정하면 scheduler가 이를 읽어 활성 환경마다 하루 한 번 실행합니다. 기본 실행 시각은
**09:00 Asia/Seoul**입니다(신규 UI 스케줄 기본값).

timezone은 실행 시각과 집계할 로컬 하루를 모두 정합니다. CLI는 기본 Asia/Seoul의
DAGSENTRY_REPORT_TIMEZONE을 사용합니다. Scheduler는 기존 세션 팩토리를 재사용합니다.
생성 후 늦게 완료된 진단이나 시간대 변경으로 저장 리포트를 재계산하지 않습니다.

0020은 기존 v1의 UTC 기간을 보존하고 빈 top_failures로 v2로 이관합니다. 빈 목록은 실패 없음과
구분합니다. 비UTC 리포트가 있으면 v1 downgrade를 거부하며 이전 백업과 대응 이미지로 복원해야 합니다.

영문 원문은 [Daily Report v2](daily-report.md)에서 확인할 수 있습니다.
