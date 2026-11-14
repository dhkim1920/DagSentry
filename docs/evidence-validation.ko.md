# AI Evidence 검증

LLM 응답은 애플리케이션 측 Evidence 검증을 통과하기 전까지 유효한 DagSentry Diagnosis가 아닙니다.
AI orchestration 계층이 결과를 노출하기 전에 검증을 실행합니다.

## 검증 규칙

모든 AI 응답은 다음을 충족해야 합니다.

- classification과 필수 `assessment`를 포함하는 `AIDiagnosisResponse`를 만족한다.
- `DIRECT`는 확인된 사실을 하나 이상, `INFERRED`와 `UNDETERMINED`는 부족한 정보와 다음 확인을 포함한다.
- `UNDETERMINED`가 아닌 결론에는 유효한 Evidence가 하나 이상 있다.
- 제공한 모든 Evidence `line_id`가 Provider에 보낸 발췌문에 존재하고, `text`가 대응하는 마스킹된 줄과
  정확히 일치한다. 판단을 보류하더라도 잘못된 인용은 허용하지 않는다.
- Secret masker를 다시 적용해도 Root Cause, Evidence, 권장 조치와 assessment 설명이 바뀌지 않는다.
- 생성된 설명이 제한된 비밀값 원문 요청 패턴과 일치하지 않는다.

알 수 없는 줄, 수정된 텍스트, 필수 근거 누락, Secret 재노출, 탐지된 비밀값 요청은
`validation_status=REJECTED`로 만듭니다. `UNDETERMINED`는 빈 Evidence를 허용합니다.
비밀값 요청 검사는 제한된 한국어·영어 패턴을 사용하며 모든 의미 오류나 프롬프트 주입을 탐지하지는
않습니다. 인용 일치는 진단 정확도를 입증하지 않습니다.

저장 전 문자열을 포함하는 응답 전체를 다시 마스킹합니다. `UNDETERMINED`의 원인 설명은 일반적인
한국어 판단 보류 문구로 바꾸고 `retry_decision=UNKNOWN`으로 만듭니다. `DIRECT`가 아닌 판단은 운영자
검토를 요구합니다. 관찰된 오류 유형은 원인 확실성과 독립적으로 유지합니다. 다른 재시도 권고도
일시적 장애와 안전한 재실행 근거가 있는지 별도 검토해야 합니다.

## 저장과 fallback

통과 응답은 `AI/PASSED` Diagnosis 한 건으로 저장합니다. 거부 응답과 `RULE` fallback은 하나의 DB
트랜잭션으로 저장합니다. 거부 AI 행에는 마스킹한 내용과 안정적인 `validation_errors`를 기록하며,
Rule 행이 유효 결과입니다. 과거 재사용 API는 거부된 행을 제외합니다. Worker는 시그니처만으로
재사용하지 않고 새 Task Try마다 현재 근거로 진단합니다.

잘못된 JSON이나 스키마 필드는 Provider 경계에서 거부되어 안전하지 않은 원시 응답을 저장하지 않고
Rule fallback이 됩니다.

영문 원문: [AI Evidence Validation](evidence-validation.md)
