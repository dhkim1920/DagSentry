# Rule Diagnosis

DagSentry는 AI diagnosis보다 먼저 결정적인 rule을 실행합니다. rule은 마스킹된 로그 evidence를 구조화
정보로 축소하며 자연어 Root Cause를 만들지 않습니다.

## 결과 계약

모든 결과에는 버전 Core enum의 classification, 일치한 rule ID와 ruleset version, 숫자 confidence와
재현 가능한 confidence 사유, 추출한 name/value pair, Relevant Log Excerpt에 존재하는 evidence `lineId`가
포함됩니다. 일치 rule이 없으면 engine은 `UNKNOWN`, `unknown.v1`, confidence `0.0`, evidence 없음을
반환합니다. Airflow log가 없어도 pipeline을 멈추지 않는 일반 Rule Diagnosis 경로입니다.

## v0.1 rule과 충돌

rule은 함께 평가하며 선택 순서는 높은 priority, priority가 같으면 높은(더 최근) evidence `lineId`, 마지막으로
사전식으로 작은 rule ID입니다.

| 우선순위 | Rule | Classification |
| ---: | --- | --- |
| 100 | 명시적 HTTP 401 | `AUTHENTICATION` |
| 100 | 명시적 HTTP 403 | `AUTHORIZATION` |
| 90 | `OOMKilled`, `out of memory`, `MemoryError` | `RESOURCE` |
| 80 | Oracle `ORA-12541` | `SOURCE_DATABASE` |
| 50 | 애플리케이션 stack frame이 있는 Python exception | `DAG_CODE` |

HTTP rule은 명시적인 HTTP/status 표현식이 필요하므로 무관한 row count나 port number와 일치하지 않습니다.
Python rule은 application frame이 필요하므로 library-only traceback을 DAG code로 분류하지 않습니다. 구체적인
infrastructure 또는 external-system evidence가 일반 Python exception보다 우선합니다.

영문 원문: [Rule Diagnosis](rule-diagnosis.md)
