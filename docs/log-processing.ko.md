# 로그 마스킹과 관련 발췌문

DagSentry는 완전한 raw Task log를 diagnosis provider에 보내지 않습니다. 사용 가능한 Task Try log는 다음
결정적 pipeline을 통과합니다.

```text
Secret masking → Airflow noise 제거 → 관련 evidence 선택 → dynamic-value 정규화 → 제한된 excerpt와 lineId 부여
```

마스킹이 항상 먼저 수행됩니다. 기본 rule은 password, token, API key, client secret, Authorization header,
connection URI credential을 다룹니다. 배포별 값은 Python 정규식 JSON 배열로 추가합니다.

```shell
DAGSENTRY_LOG_SECRET_PATTERNS='["tenant-secret-[A-Za-z0-9]+"]'
```

전체 일치는 `[REDACTED]`로 바꾸며 잘못된 custom expression은 마스킹을 약화시키지 않도록 processor 시작 시
실패합니다. 기본 입력 제한은 1,048,576자, 결과는 최대 80줄·총 16,000 line character입니다. Evidence는 원래
순서를 유지하고 1부터 시작하는 raw-log line number를 `lineId`로 씁니다. signal이 없으면 sparse log도 진단할 수
있도록 마지막 10개 non-noise line을 보관합니다. timestamp, UUID, labeled numeric ID, 긴 숫자, temporary path,
stack-frame line number, hexadecimal address를 정규화하며 이후 Rule/AI 단계에는 이 마스킹·정규화 excerpt만
노출하고 raw log는 현재 구현에서 저장하지 않습니다.

영문 원문: [Log Sanitization and Relevant Excerpt](log-processing.md)
