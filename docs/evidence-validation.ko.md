# AI Evidence 검증

LLM 응답은 애플리케이션 측 Evidence 검증을 통과하기 전까지 유효한 DagSentry Diagnosis가 아닙니다.
AI orchestration 계층이 결과를 노출하기 전에 이를 실행하므로 호출자가 실수로 건너뛸 수 없습니다.

## 검증 규칙

모든 AI 응답은 다음을 충족해야 합니다.

- 구성한 JSON Schema와 classification Enum을 만족한다.
- Root Cause를 뒷받침하는 Evidence가 하나 이상 있다.
- 모든 Evidence `line_id`가 Provider에 보낸 정확한 Relevant Log Excerpt에 존재한다.
- 모든 Evidence `text`가 대응하는 마스킹된 발췌문 줄과 정확히 일치한다.
- 설정한 Secret masker를 다시 적용해도 Root Cause, Evidence, 권장 조치가 바뀌지 않는다.

알 수 없는 줄, 수정된 텍스트, 근거 누락, Secret 재노출은 `validation_status=REJECTED`로 만듭니다.
저장 전에는 문자열을 포함하는 AI 출력 전체를 다시 마스킹하므로 재노출된 원본 Secret은 저장하지 않습니다.

## 저장과 fallback

통과 응답은 `validation_status=PASSED`인 `AI` Diagnosis 한 건으로 저장합니다. 거부 응답과 그 `RULE`
fallback은 하나의 DB 트랜잭션으로 저장합니다. 거부 AI 행에는 정제된 내용과 안정적인 `validation_errors`
코드만 기록하며 Rule 행이 유효 fallback입니다. 유효 구조화 AI 출력의 `operator_review_required`는
유지하고, 재사용 조회는 `PASSED` 행만 선택하므로 거부 AI 내용은 재사용되지 않습니다. 잘못된 JSON 또는
Enum은 Provider 경계에서 거부되어 안전하지 않은 원시 응답을 만들지 않고 Rule fallback이 됩니다.

영문 원문: [AI Evidence Validation](evidence-validation.md)
