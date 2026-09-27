"use strict";

const LANGUAGE_STORAGE_KEY = "dagsentry.language";
const TIMEZONE_STORAGE_KEY = "dagsentry.timezone";
const PAGE_SIZE = 20;
const SIGNATURE_PAGE_SIZE = 20;
const OCCURRENCE_PAGE_SIZE = 20;
const DIAGNOSIS_PAGE_SIZE = 20;
const REPORT_PAGE_SIZE = 20;

function generateUuid() {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

const KOREAN_TRANSLATIONS = Object.freeze({
  "Incident response": "장애 대응",
  "Error patterns": "오류 패턴",
  "Settings": "설정",
  "Find an incident, review its cause, and decide the next action.": "장애를 찾고 원인을 확인한 뒤 다음 조치를 결정합니다.",
  "Find recurring failures with the same error pattern.": "같은 오류 패턴으로 반복되는 실패를 확인합니다.",
  "Manage users, external connections, and administration history.": "사용자, 외부 연결, 관리자 변경 이력을 관리합니다.",
  "Review daily failures, recommended actions, and report delivery.": "일별 장애와 권장 조치, 리포트 전송 결과를 확인합니다.",
  "Error summary": "오류 요약",
  "Error summary unavailable": "오류 요약 없음",
  "Status totals use the same environment, DAG and task filters, regardless of the selected status.": "대응 대기·조사 중 건수는 선택한 상태와 무관하게 같은 환경·DAG·태스크 조건으로 집계합니다.",
  "More filters and sorting": "상세 필터 및 정렬",
  "Report delivery settings": "리포트 발송 설정",
  "Automatic diagnosis for the latest failure": "최신 실패의 자동 진단",
  "No validated diagnosis is available for the latest failure. Review its task log or earlier attempts in the history.": "최신 실패의 유효한 진단이 아직 없습니다. 태스크 로그 또는 이전 실패 이력을 확인하세요.",
  "Evidence and diagnosis details": "근거 및 진단 상세",
  "Confidence is the diagnosis score, not a measured accuracy rate.": "신뢰도는 진단이 제시한 점수이며, 측정된 정확도가 아닙니다.",
  "Validation describes automated checks, not operator confirmation of the cause. AI checks include matching cited evidence to the log.": "검증 상태는 자동 검사의 결과이며 운영자의 원인 확정을 뜻하지 않습니다. AI 검증에는 인용 근거와 로그의 일치 여부가 포함됩니다.",
  "This incident is awaiting investigation.": "아직 조사를 시작하지 않은 장애입니다.",
  "An operator has started investigating this incident.": "운영자가 확인하고 조사 중인 장애입니다.",
  "The task has recovered. Confirm whether follow-up is complete before resolving the incident.": "태스크 실행이 복구되었습니다. 후속 조치가 끝났는지 확인한 뒤 처리를 완료하세요.",
  "An operator has closed this incident after confirming remediation.": "운영자가 조치 완료를 확인하고 종결한 장애입니다.",
  "An operator has closed this incident without further action.": "운영자가 추가 조치 없이 종결한 장애입니다.",
  "DagSentry — Incident operations": "DagSentry — 인시던트 운영",
  "DagSentry — Connect": "DagSentry — 연결",
  "DagSentry — Incident response": "DagSentry — 장애 대응",
  "DagSentry — Incident detail": "DagSentry — 인시던트 상세",
  "DagSentry — Error patterns": "DagSentry — 오류 패턴",
  "DagSentry — Error Signature detail": "DagSentry — 오류 시그니처 상세",
  "DagSentry — Diagnosis History": "DagSentry — 진단 이력",
  "DagSentry — Diagnosis detail": "DagSentry — 진단 상세",
  "DagSentry — Settings": "DagSentry — 설정",
  "DagSentry — Daily Reports": "DagSentry — 데일리 리포트",
  "DagSentry — Daily Report detail": "DagSentry — 데일리 리포트 상세",
  "DagSentry — Change temporary password": "DagSentry — 임시 비밀번호 변경",
  "Skip to content": "본문으로 건너뛰기",
  "Primary navigation": "주요 탐색",
  "DagSentry home": "DagSentry 홈",
  Language: "언어",
  Incidents: "인시던트",
  "Error Signatures": "오류 시그니처",
  "Diagnosis History": "진단 이력",
  Reports: "리포트",
  "Review immutable UTC Statistics, rule-based guidance, optional AI summaries, and delivery outcomes.": "불변 UTC 통계, 규칙 기반 안내, 선택적 AI 요약과 전송 결과를 확인합니다.",
  "Daily Report summary": "데일리 리포트 요약",
  "Total Reports": "전체 리포트",
  Delivered: "전송 완료",
  Failed: "실패",
  "AI-assisted": "AI 보조",
  "Daily Report filters": "데일리 리포트 필터",
  "Delivery status": "전송 상태",
  "Report date from": "리포트 시작일",
  "Report date to": "리포트 종료일",
  Pending: "대기 중",
  "Loading Daily Reports…": "데일리 리포트를 불러오는 중…",
  "No Daily Reports match these filters": "필터와 일치하는 데일리 리포트가 없습니다",
  "Clear a filter or widen the report date range.": "필터를 지우거나 리포트 날짜 범위를 넓혀보세요.",
  "Daily Reports": "데일리 리포트",
  "Daily Report results": "데일리 리포트 결과",
  "Daily Report results table": "데일리 리포트 결과 표",
  "Daily Report pages": "데일리 리포트 페이지",
  "Report date": "리포트 날짜",
  "DAG runs": "DAG 실행",
  Unresolved: "미해결",
  Delivery: "전송",
  "← Back to Daily Reports": "← 데일리 리포트 목록으로",
  "Loading Daily Report detail…": "데일리 리포트 상세를 불러오는 중…",
  "Failure attempts": "실패 시도",
  "Task instances": "태스크 인스턴스",
  Overview: "개요",
  Highlights: "주요 내용",
  Priorities: "우선순위",
  "AI summary": "AI 요약",
  "Key changes": "주요 변화",
  "AI priorities": "AI 우선순위",
  "Daily Report metadata": "데일리 리포트 메타데이터",
  "Error classifications": "오류 분류",
  "Rule-based": "규칙 기반",
  New: "신규",
  Attempts: "시도 횟수",
  "HTTP status": "HTTP 상태",
  "Last error": "마지막 오류",
  "New signatures": "신규 시그니처",
  "Repeated signatures": "반복 시그니처",
  "Mean recovery": "평균 복구 시간",
  "Mean resolution": "평균 해결 시간",
  "No classified failures": "분류된 실패 없음",
  Menu: "메뉴",
  "Collapse navigation": "메뉴 접기",
  "Expand navigation": "메뉴 펼치기",
  "Not connected": "연결되지 않음",
  "Log out": "로그아웃",
  "See the failure.": "실패를 확인하고.",
  "Trace the evidence.": "근거를 추적하세요.",
  Timezone: "시간대",
  "Browser local": "브라우저 현지 시간",
  "Sign in to DagSentry": "DagSentry 로그인",
  Email: "이메일",
  Password: "비밀번호",
  "Sign in": "로그인",
  "Signing in…": "로그인 중…",
  "Loading DagSentry…": "DagSentry를 불러오는 중…",
  "Change temporary password": "임시 비밀번호 변경",
  "Current temporary password": "현재 임시 비밀번호",
  "New password": "새 비밀번호",
  "Confirm new password": "새 비밀번호 확인",
  "Change password": "비밀번호 변경",
  "Passwords do not match.": "새 비밀번호가 일치하지 않습니다.",
  "Password changed. Sign in again.": "비밀번호가 변경되었습니다. 다시 로그인하세요.",
  "Unable to reach DagSentry.": "DagSentry에 연결할 수 없습니다.",
  "Managed users": "관리 사용자",
  "Create local user": "로컬 사용자 생성",
  "Display name": "표시 이름",
  Role: "역할",
  "Temporary password": "임시 비밀번호",
  "Create user": "사용자 생성",
  "Loading user administration…": "사용자 관리를 불러오는 중…",
  "User list": "사용자 목록",
  "Last login": "마지막 로그인",
  "Administrator audit history": "관리자 감사 이력",
  "Administrator audit history table": "관리자 감사 이력 표",
  Time: "시각",
  Actor: "수행자",
  Target: "대상",
  Summary: "요약",
  "Account recovery": "계정 복구",
  "Reset temporary password": "임시 비밀번호 초기화",
  "Reset password": "비밀번호 초기화",
  "Password change required": "비밀번호 변경 필요",
  Never: "없음",
  "Save role": "역할 저장",
  Disable: "비활성화",
  Enable: "활성화",
  "Revoke sessions": "세션 폐기",
  "Live operational queue": "실시간 운영 대기열",
  "Matching incidents": "일치하는 인시던트",
  "Incident filters": "인시던트 필터",
  Status: "상태",
  "All statuses": "모든 상태",
  Open: "대응 대기",
  Acknowledged: "조사 중",
  Recovered: "실행 복구됨",
  Resolved: "처리 완료",
  Ignored: "무시됨",
  Environment: "환경",
  Task: "태스크",
  "Sort by": "정렬 기준",
  "Latest activity": "최근 활동",
  "Failure count": "실패 횟수",
  Created: "생성 시각",
  "State updated": "상태 변경 시각",
  Order: "정렬 순서",
  Descending: "내림차순",
  Ascending: "오름차순",
  "Apply filters": "필터 적용",
  Clear: "초기화",
  "Loading incidents…": "인시던트를 불러오는 중…",
  "No incidents match these filters": "필터와 일치하는 인시던트가 없습니다",
  "Clear a filter or choose another status to widen the operational view.": "필터를 지우거나 다른 상태를 선택해 조회 범위를 넓혀보세요.",
  "Incident list": "인시던트 목록",
  "Incident results": "인시던트 결과",
  "Incident results table": "인시던트 결과 표",
  "Incident pages": "인시던트 페이지",
  "DAG / Task": "DAG / 태스크",
  Failures: "실패",
  "First seen": "최초 발생",
  "Last activity": "최근 실패",
  Actions: "작업",
  Previous: "이전",
  Next: "다음",
  "Recurring failure identity": "반복 실패 식별",
  "Matching signatures": "일치하는 시그니처",
  "Error Signature filters": "오류 시그니처 필터",
  Search: "검색",
  Classification: "분류",
  "All classifications": "모든 분류",
  "DAG code": "DAG 코드",
  "Airflow platform": "Airflow 플랫폼",
  "Source database": "원본 데이터베이스",
  Network: "네트워크",
  Authentication: "인증",
  Authorization: "권한",
  Resource: "리소스",
  "Data quality": "데이터 품질",
  "External system": "외부 시스템",
  Configuration: "설정",
  Unknown: "알 수 없음",
  "Observed from": "관측 시작일",
  "Observed to": "관측 종료일",
  "Latest occurrence": "최근 발생",
  "Incident count": "인시던트 수",
  "Loading Error Signatures…": "오류 시그니처를 불러오는 중…",
  "No Error Signatures match these filters": "필터와 일치하는 오류 시그니처가 없습니다",
  "Clear a filter or widen the observation range.": "필터를 지우거나 관측 범위를 넓혀보세요.",
  "Error Signature results": "오류 시그니처 결과",
  "Error Signature results table": "오류 시그니처 결과 표",
  "Error Signature pages": "오류 시그니처 페이지",
  Signature: "시그니처",
  "Operator / Code": "오퍼레이터 / 코드",
  "Operator / Exception": "오퍼레이터 / 예외",
  "Last seen": "최근 발생",
  "Auditable reasoning provenance": "감사 가능한 진단 근거",
  "Review Rule- and AI-based diagnosis history, including reused and rejected results and supporting provenance.": "규칙 및 AI 기반 진단 이력과 재사용, 거부 결과, 참고 출처를 확인합니다.",
  "Search AI, Rule, and operator-authored diagnosis records with their Incident and Error Signature context.": "AI, 규칙 및 운영자 작성 진단 기록을 인시던트와 오류 시그니처 맥락과 함께 검색합니다.",
  "Matching diagnoses": "일치하는 진단",
  "Diagnosis summary": "진단 요약",
  "Total Diagnoses": "전체 진단",
  "Diagnosis History filters": "진단 이력 필터",
  Source: "출처",
  "All sources": "모든 출처",
  Rule: "규칙",
  Reused: "재사용",
  Validation: "검증",
  "All validation states": "모든 검증 상태",
  Passed: "통과",
  Rejected: "거부",
  "Error Signature ID": "오류 시그니처 ID",
  "Failure Event ID": "실패 이벤트 ID",
  "Signature ID": "시그니처 ID",
  "Created from": "생성 시작일",
  "Created to": "생성 종료일",
  Confidence: "신뢰도",
  "Loading Diagnosis History…": "진단 이력을 불러오는 중…",
  "No Diagnoses match these filters": "필터와 일치하는 진단이 없습니다",
  "Clear a filter or widen the creation range.": "필터를 지우거나 생성 범위를 넓혀보세요.",
  "Diagnosis History results": "진단 이력 결과",
  "Diagnosis History results table": "진단 이력 결과 표",
  "Diagnosis History pages": "진단 이력 페이지",
  Diagnosis: "진단",
  "Effective provenance": "대표 진단 출처",
  "Confidence / Review": "신뢰도 / 검토",
  "← Back to incidents": "← 인시던트 목록으로",
  "Loading Incident detail…": "인시던트 상세를 불러오는 중…",
  "Failure tries": "실패 횟수",
  "Failure & Diagnosis history": "실패 및 진단 이력",
  "Operator-confirmed diagnosis": "운영자 확정 진단",
  "Operator diagnosis history": "운영자 진단 이력",
  "No operator-confirmed diagnosis has been published.": "등록된 운영자 확정 진단이 없습니다.",
  "Create revision": "수정본 만들기",
  Edit: "수정",
  "Add diagnosis": "진단 추가",
  Withdraw: "철회",
  "Publish operator-confirmed diagnosis": "운영자 확정 진단 등록",
  "Root cause": "근본 원인",
  "One per line": "줄마다 하나씩",
  "Retry advice": "재시도 판단",
  "Operator notes": "운영자 메모",
  "Change reason": "변경 사유",
  "Required after the first revision": "첫 번째 수정본 이후 필수",
  "Publish diagnosis": "진단 등록",
  "Reason for withdrawal:": "철회 사유:",
  "Operator-confirmed diagnosis published.": "운영자 확정 진단을 등록했습니다.",
  "Operator-confirmed diagnosis withdrawn.": "운영자 확정 진단을 철회했습니다.",
  "Unable to publish diagnosis": "진단을 등록할 수 없습니다.",
  "Unable to withdraw diagnosis": "진단을 철회할 수 없습니다.",
  "Change state": "상태 변경",
  "Read-only access": "읽기 전용 접근",
  "State changes unavailable": "상태 변경 불가",
  "Sign in with an Operator or Admin account to change this Incident.": "이 인시던트를 변경하려면 Operator 또는 Admin 계정으로 로그인하세요.",
  "State history": "상태 이력",
  "No state transitions have been recorded.": "기록된 상태 변경이 없습니다.",
  "Incident ID": "인시던트 ID",
  "Failures are grouped by environment, DAG, Task, and versioned Error Signature.": "실패는 환경, DAG, 태스크 및 버전이 지정된 오류 시그니처로 그룹화됩니다.",
  "← Back to Error Signatures": "← 오류 시그니처 목록으로",
  "Loading Error Signature detail…": "오류 시그니처 상세를 불러오는 중…",
  "Occurrence trend": "발생 추이",
  Occurrences: "발생 내역",
  "No occurrences match the preserved filters.": "유지된 필터와 일치하는 발생 내역이 없습니다.",
  "Error Signature occurrences": "오류 시그니처 발생 내역",
  "Error Signature occurrence table": "오류 시그니처 발생 표",
  "Signature occurrence pages": "시그니처 발생 페이지",
  Incident: "인시던트",
  "Run information": "실행 정보",
  "Failure time": "발생 시각",
  Observed: "관측 시각",
  Identity: "식별 정보",
  "Diagnosis context": "진단 컨텍스트",
  "Error Signature context": "오류 시그니처 컨텍스트",
  "Signature Detail": "시그니처 상세",
  "7 days": "7일",
  "30 days": "30일",
  "Trend range": "추이 기간",
  "Status / Incident": "상태 / 인시던트",
  "Latest Verified Diagnosis": "최신 검증 진단",
  "Signature Metadata": "시그니처 메타데이터",
  "← Back to Diagnosis History": "← 진단 이력으로",
  "Loading Diagnosis detail…": "진단 상세를 불러오는 중…",
  "Diagnosis Detail": "진단 상세",
  Retry: "재시도",
  Review: "운영자 검토",
  "Failure run information": "실패 실행 정보",
  "Root Cause": "근본 원인",
  "Relevant Log": "관련 로그",
  "Full task logs remain in Airflow and are not copied into DagSentry.": "전체 태스크 로그는 Airflow에 유지되며 DagSentry로 복사되지 않습니다.",
  "Similar Diagnosis": "유사 진단 이력",
  "Diagnosis Metadata": "진단 메타데이터",
  "Linked Incident": "연결된 인시던트",
  "Technical details": "기술 상세 정보",
  "Record identity": "레코드 식별 정보",
  "Error Signature": "오류 시그니처",
  "Related Diagnosis": "관련 진단",
  "Diagnosis provenance": "진단 출처",
  "Confirm operator action": "Operator 작업 확인",
  Reason: "사유",
  Optional: "선택 사항",
  Cancel: "취소",
  Confirm: "확인",
  Close: "닫기",
  Inspect: "확인",
  Explore: "탐색",
  "Unclassified Error Signature": "분류되지 않은 오류 시그니처",
  "No normalized message": "정규화된 메시지 없음",
  "Unknown operator": "알 수 없는 오퍼레이터",
  "No vendor code": "벤더 코드 없음",
  "No exception or vendor code": "예외 및 벤더 코드 없음",
  "No validated original Diagnosis is available.": "사용 가능한 검증된 원본 진단이 없습니다.",
  "No Root Cause was produced.": "생성된 근본 원인이 없습니다.",
  "No Root Cause was produced": "생성된 근본 원인이 없습니다",
  "Inspect Diagnosis": "상세 보기",
  Fingerprint: "시그니처 ID",
  "Fingerprint version": "시그니처 버전",
  Copy: "복사",
  Copied: "복사됨",
  "Copy failed": "복사 실패",
  Operator: "오퍼레이터",
  Exception: "예외",
  "Vendor code": "벤더 코드",
  "Stack frame": "스택 프레임",
  Unavailable: "사용 불가",
  Effective: "대표 진단",
  Original: "원본",
  "Not effective": "대표 진단 아님",
  "No effective result": "대표 진단 없음",
  "Not an effective Diagnosis": "대표 진단 아님",
  "Confidence unavailable": "신뢰도 정보 없음",
  "Review unavailable": "검토 정보 없음",
  "Operator review required": "Operator 검토 필요",
  "No review required": "검토 불필요",
  "Operator required": "Operator 필요",
  "Not required": "필요 없음",
  "Reasoning metadata": "근거 메타데이터",
  "Confidence reason": "신뢰도 근거",
  "Matched rule": "일치한 규칙",
  "Extracted values": "추출된 값",
  "DAG run": "DAG 실행",
  Try: "시도",
  "Map index": "맵 인덱스",
  State: "상태",
  "Inspect related Incident": "인시던트 보기",
  "View details": "상세 보기",
  "Open Task log in Airflow ↗": "Airflow에서 태스크 로그 열기 ↗",
  "Diagnosis ID": "진단 ID",
  "Content Diagnosis ID": "콘텐츠 진단 ID",
  Schema: "스키마",
  Prompt: "프롬프트",
  "Open original Diagnosis": "원본 진단 열기",
  "Open effective Diagnosis": "대표 진단 보기",
  "Explore Error Signature": "오류 시그니처 보기",
  "No Error Signature is available.": "연결된 오류 시그니처가 없습니다.",
  "No linked Incident is available.": "연결된 인시던트가 없습니다.",
  "No other Diagnosis exists for this Error Signature.": "이 오류 시그니처의 다른 진단이 없습니다.",
  "Loading Similar Diagnosis…": "유사 진단 이력을 불러오는 중…",
  "Similar Diagnosis requires an Error Signature.": "유사 진단 조회에는 오류 시그니처가 필요합니다.",
  "Unable to load Similar Diagnosis.": "유사 진단 이력을 불러올 수 없습니다.",
  Evidence: "근거",
  "No log Evidence was retained for this Diagnosis.": "이 진단에 보관된 로그 근거가 없습니다.",
  "Recommended actions": "권장 조치",
  "Check the target service health.": "대상 서비스 상태를 확인합니다.",
  "Confirm port 6543 is listening.": "6543 포트가 LISTEN 상태인지 확인합니다.",
  "Verify worker network connectivity.": "Worker 네트워크 연결을 확인합니다.",
  "Review NetworkPolicy and firewall rules.": "NetworkPolicy와 방화벽 규칙을 확인합니다.",
  "Rejected AI attempt — retained for provenance and never used as the effective Diagnosis.": "거부된 AI 시도 — 출처 확인을 위해 보관되며 대표 진단으로 사용되지 않습니다.",
  "Validation errors": "검증 오류",
  Version: "버전",
  Message: "메시지",
  "Explore recurring occurrences →": "동일 오류 발생 이력 →",
  "Diagnosis attempts": "진단 결과",
  "Version details": "버전 정보",
  "Diagnosis has not been persisted yet.": "진단이 아직 저장되지 않았습니다.",
  "Unsignable Failure — no stable Error Signature was available for correlation.": "시그니처 생성 불가 — 연관 분석에 사용할 안정적인 오류 시그니처가 없습니다.",
  "Choose an explicit state change. Every change is added to the audit trail.": "변경할 상태를 선택하세요. 모든 변경은 감사 기록에 추가됩니다.",
  "This Incident is in a terminal state. No further state changes are available.": "이 인시던트는 종료 상태이며 더 이상 상태를 변경할 수 없습니다.",
  "This terminal Incident can be changed by its last operator or an Admin.": "이 종료 인시던트는 마지막 처리자 또는 Admin이 다시 변경할 수 있습니다.",
  "Only the operator who made the terminal change or an Admin can change this Incident.": "이 종료 인시던트는 마지막 처리자 또는 Admin만 변경할 수 있습니다.",
  "Only the operator who made the terminal change or an Admin may change it": "마지막 처리자 또는 Admin만 이 종료 인시던트를 변경할 수 있습니다.",
  "Another active Incident already exists for this failure group": "동일한 실패 그룹에 활성 인시던트가 이미 존재합니다.",
  Reopen: "다시 열기",
  "Reopen this Incident for active investigation.": "이 인시던트를 다시 열어 조사를 계속합니다.",
  Acknowledge: "조사 시작",
  Resolve: "처리 완료",
  Ignore: "무시",
  "Mark this Incident as actively investigated.": "이 인시던트를 조사 중 상태로 표시합니다.",
  "Close this Incident after confirming remediation is complete.": "조치 완료를 확인한 후 이 인시던트를 종료합니다.",
  "Close this Incident without further operational work.": "추가 운영 작업 없이 이 인시던트를 종료합니다.",
  "Confirm the recovered Incident requires no further operational work.": "복구된 인시던트에 추가 운영 작업이 필요하지 않은지 확인합니다.",
  "Close this recovered Incident without further operational work.": "추가 운영 작업 없이 복구된 인시던트를 종료합니다.",
  "Unable to load Error Signatures": "오류 시그니처를 불러올 수 없습니다",
  "Unable to load Error Signature detail": "오류 시그니처 상세를 불러올 수 없습니다",
  "Unable to load Diagnosis History": "진단 이력을 불러올 수 없습니다",
  "Unable to load Diagnosis detail": "진단 상세를 불러올 수 없습니다",
  "Unable to load Daily Reports": "데일리 리포트를 불러올 수 없습니다",
  "Unable to load Daily Report detail": "데일리 리포트 상세를 불러올 수 없습니다",
  "Unable to load incidents": "인시던트를 불러올 수 없습니다",
  "Unable to load Incident detail": "인시던트 상세를 불러올 수 없습니다",
  "Unable to change the Incident state": "인시던트 상태를 변경할 수 없습니다",
  "Enter your email and password.": "이메일과 비밀번호를 입력하세요.",
  "External connections": "외부 연결",
  "Create or edit external connection": "외부 연결 생성 또는 수정",
  Provider: "프로바이더",
  "API base URL": "API 기본 URL",
  "UI base URL": "UI 기본 URL",
  Model: "모델",
  "Channel ID": "채널 ID",
  Token: "토큰",
  "Bot token": "봇 토큰",
  Help: "안내",
  "Secret field help": "Secret 필드 도움말",
  "Write-only. Leave blank while editing to preserve the configured Secret.": "쓰기 전용입니다. 수정할 때 비워두면 기존 Secret이 유지됩니다.",
  "No external connections yet.": "아직 등록된 외부 연결이 없습니다.",
  "Cancel edit": "수정 취소",
  "Create connection": "연결 생성",
  "Save connection": "연결 저장",
  "External connections table": "외부 연결 표",
  Connection: "연결",
  Secret: "Secret",
  "Last test": "마지막 테스트",
  "Not configured": "설정되지 않음",
  "Never tested": "테스트 안 함",
  Edit: "수정",
  Test: "테스트",
  OPEN: "대응 대기",
  ACKNOWLEDGED: "조사 중",
  RECOVERED: "실행 복구됨",
  RESOLVED: "처리 완료",
  IGNORED: "무시됨",
  REOPENED: "다시 열림",
  FAILED: "실패",
  UP_FOR_RETRY: "재시도 대기",
  RULE: "규칙",
  REUSED: "재사용",
  PASSED: "검증 통과",
  REJECTED: "거부",
  DAG_CODE: "DAG 코드",
  AIRFLOW_PLATFORM: "Airflow 플랫폼",
  SOURCE_DATABASE: "원본 데이터베이스",
  NETWORK: "네트워크",
  AUTHENTICATION: "인증",
  AUTHORIZATION: "권한",
  RESOURCE: "리소스",
  DATA_QUALITY: "데이터 품질",
  EXTERNAL_SYSTEM: "외부 시스템",
  CONFIGURATION: "설정",
  UNKNOWN: "알 수 없음",
});

let currentLanguage = localStorage.getItem(LANGUAGE_STORAGE_KEY)
  || (navigator.language.toLowerCase().startsWith("ko") ? "ko" : "en");
if (!new Set(["ko", "en"]).has(currentLanguage)) {
  currentLanguage = "en";
}
let currentTimezone = localStorage.getItem(TIMEZONE_STORAGE_KEY) || "Asia/Seoul";
if (!new Set(["Asia/Seoul", "UTC", "browser"]).has(currentTimezone)) {
  currentTimezone = "Asia/Seoul";
}
const originalText = new WeakMap();
const originalAttributes = new WeakMap();

function translatePattern(value) {
  let match = value.match(/^(\d+)–(\d+) of (\d+)$/);
  if (match) {
    return `전체 ${match[3]}개 중 ${match[1]}–${match[2]}`;
  }
  match = value.match(/^Page (\d+) of (\d+)$/);
  if (match) {
    return `${match[2]}페이지 중 ${match[1]}페이지`;
  }
  match = value.match(/^(viewer|operator) session$/);
  if (match) {
    return match[1] === "operator" ? "Operator 세션" : "Viewer 세션";
  }
  match = value.match(/^Try (\d+) · (.+)$/);
  if (match) {
    return `${match[1]}차 시도 · ${KOREAN_TRANSLATIONS[match[2]] || match[2]}`;
  }
  match = value.match(/^Occurrence (\d+)$/);
  if (match) {
    return `발생 ${match[1]}회`;
  }
  match = value.match(/^(\d+)% confidence$/);
  if (match) {
    return `신뢰도 ${match[1]}%`;
  }
  match = value.match(/^from (.+)$/);
  if (match) {
    return `원본 ${match[1]}`;
  }
  match = value.match(/^effective (.+)$/);
  if (match) {
    return `대표 진단 ${match[1]}`;
  }
  match = value.match(/^run (.+)$/);
  if (match) {
    return `실행 ${match[1]}`;
  }
  match = value.match(/^map (.+)$/);
  if (match) {
    return `맵 ${match[1]}`;
  }
  match = value.match(/^Request failed with status (\d+)$/);
  if (match) {
    return `요청 실패 (상태 ${match[1]})`;
  }
  match = value.match(/^Incident changed to (.+)\.$/);
  if (match) {
    return `인시던트 상태가 ${KOREAN_TRANSLATIONS[match[1]] || match[1]}(으)로 변경되었습니다.`;
  }
  match = value.match(/^Inspect Incident (.+)$/);
  if (match) {
    return `인시던트 ${match[1]} 확인`;
  }
  match = value.match(/^Inspect Diagnosis (.+)$/);
  if (match) {
    return `진단 ${match[1]} 확인`;
  }
  match = value.match(/^Explore (.+)$/);
  if (match) {
    return `${match[1]} 탐색`;
  }
  match = value.match(/^Inspect (.+) Incident$/);
  if (match) {
    return `${match[1]} 인시던트 확인`;
  }
  return value;
}

function translatedText(value) {
  if (currentLanguage === "en") {
    return value;
  }
  return KOREAN_TRANSLATIONS[value] || translatePattern(value);
}

function translatedValue(value) {
  const trimmed = value.trim();
  if (!trimmed) {
    return value;
  }
  return value.replace(trimmed, translatedText(trimmed));
}

function isLanguageInvariant(node) {
  const element = node instanceof Element ? node : node.parentElement;
  return element?.closest("[data-i18n-fixed]") !== null;
}

function translateElementAttributes(element) {
  let attributes = originalAttributes.get(element);
  if (!attributes) {
    attributes = new Map();
    originalAttributes.set(element, attributes);
  }
  for (const name of ["aria-label", "placeholder", "title"]) {
    if (element.hasAttribute(name) && !attributes.has(name)) {
      attributes.set(name, element.getAttribute(name));
    }
    if (attributes.has(name)) {
      element.setAttribute(name, translatedText(attributes.get(name)));
    }
  }
}

function translateSubtree(root) {
  if (isLanguageInvariant(root)) {
    return;
  }
  if (root.nodeType === Node.TEXT_NODE) {
    if (!originalText.has(root)) {
      originalText.set(root, root.nodeValue);
    }
    root.nodeValue = translatedValue(originalText.get(root));
    return;
  }
  if (root instanceof Element) {
    translateElementAttributes(root);
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    if (isLanguageInvariant(node)) {
      node = walker.nextNode();
      continue;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      if (!originalText.has(node)) {
        originalText.set(node, node.nodeValue);
      }
      node.nodeValue = translatedValue(originalText.get(node));
    } else {
      translateElementAttributes(node);
    }
    node = walker.nextNode();
  }
}

function applyLanguage() {
  document.documentElement.lang = currentLanguage;
  document.querySelector("#language-select").value = currentLanguage;
  document.querySelector("#timezone-select").value = currentTimezone;
  translateSubtree(document.body);
  applySidebarState();
}

const OPERATOR_ACTIONS = {
  OPEN: [
    {
      status: "ACKNOWLEDGED",
      label: "Acknowledge",
      copy: "Mark this Incident as actively investigated.",
    },
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Close this Incident after confirming remediation is complete.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this Incident without further operational work.",
    },
  ],
  ACKNOWLEDGED: [
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Close this Incident after confirming remediation is complete.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this Incident without further operational work.",
    },
  ],
  RECOVERED: [
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Confirm the recovered Incident requires no further operational work.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this recovered Incident without further operational work.",
    },
  ],
  RESOLVED: [],
  IGNORED: [],
};

const TERMINAL_OPERATOR_ACTIONS = {
  RESOLVED: [
    {
      status: "OPEN",
      label: "Reopen",
      copy: "Reopen this Incident for active investigation.",
    },
    {
      status: "ACKNOWLEDGED",
      label: "Acknowledge",
      copy: "Mark this Incident as actively investigated.",
    },
    {
      status: "IGNORED",
      label: "Ignore",
      copy: "Close this Incident without further operational work.",
    },
  ],
  IGNORED: [
    {
      status: "OPEN",
      label: "Reopen",
      copy: "Reopen this Incident for active investigation.",
    },
    {
      status: "ACKNOWLEDGED",
      label: "Acknowledge",
      copy: "Mark this Incident as actively investigated.",
    },
    {
      status: "RESOLVED",
      label: "Resolve",
      copy: "Close this Incident after confirming remediation is complete.",
    },
  ],
};

const MANAGED_CONNECTION_PROVIDERS = Object.freeze({
  AIRFLOW: {
    purpose: "AIRFLOW",
    apiBaseUrl: "http://localhost:8080/api/v2",
    secretField: "token",
  },
  OLLAMA: {
    purpose: "LLM",
    apiBaseUrl: "http://localhost:11434/api",
    secretField: null,
  },
  SLACK: {
    purpose: "NOTIFICATION",
    apiBaseUrl: "https://slack.com/api",
    secretField: "bot_token",
  },
});

const elements = {
  authPanel: document.querySelector("#auth-panel"),
  authForm: document.querySelector("#auth-form"),
  authLoading: document.querySelector("#auth-loading"),
  sessionLoading: document.querySelector("#session-loading"),
  authSubmit: document.querySelector("#auth-submit"),
  authError: document.querySelector("#auth-error"),
  accessEmail: document.querySelector("#access-email"),
  accessPassword: document.querySelector("#access-password"),
  changePasswordForm: document.querySelector("#change-password-form"),
  currentPassword: document.querySelector("#current-password"),
  newPassword: document.querySelector("#new-password"),
  confirmNewPassword: document.querySelector("#confirm-new-password"),
  changePasswordError: document.querySelector("#change-password-error"),
  languageSelect: document.querySelector("#language-select"),
  timezoneSelect: document.querySelector("#timezone-select"),
  sidebarToggle: document.querySelector("#sidebar-toggle"),
  sidebarToggleIcon: document.querySelector("#sidebar-toggle-icon"),
  siteSidebar: document.querySelector("#site-sidebar"),
  primaryNavigation: document.querySelector("#primary-navigation"),
  incidentsNav: document.querySelector("#incidents-nav"),
  signaturesNav: document.querySelector("#signatures-nav"),
  diagnosesNav: document.querySelector("#diagnoses-nav"),
  reportsNav: document.querySelector("#reports-nav"),
  adminNav: document.querySelector("#admin-nav"),
  dashboard: document.querySelector("#incident-dashboard"),
  disconnect: document.querySelector("#disconnect-button"),
  sessionStatus: document.querySelector("#session-status"),
  filterForm: document.querySelector("#filter-form"),
  clearFilters: document.querySelector("#clear-filters"),
  refreshIncidents: document.querySelector("#refresh-incidents"),
  total: document.querySelector("#incident-total"),
  openTotal: document.querySelector("#incident-open-total"),
  acknowledgedTotal: document.querySelector("#incident-acknowledged-total"),
  loading: document.querySelector("#loading-state"),
  error: document.querySelector("#dashboard-error"),
  empty: document.querySelector("#empty-state"),
  results: document.querySelector("#results-panel"),
  range: document.querySelector("#results-range"),
  rows: document.querySelector("#incident-rows"),
  previous: document.querySelector("#previous-page"),
  next: document.querySelector("#next-page"),
  pageLabel: document.querySelector("#page-label"),
  detail: document.querySelector("#incident-detail"),
  detailBack: document.querySelector("#detail-back"),
  detailLoading: document.querySelector("#detail-loading"),
  detailError: document.querySelector("#detail-error"),
  detailFeedback: document.querySelector("#detail-feedback"),
  detailContent: document.querySelector("#detail-content"),
  detailStatus: document.querySelector("#detail-status"),
  detailStateHelp: document.querySelector("#detail-state-help"),
  currentDiagnosisPanel: document.querySelector("#current-diagnosis-panel"),
  currentDiagnosisContent: document.querySelector("#current-diagnosis-content"),
  detailEnvironment: document.querySelector("#detail-environment"),
  detailTitle: document.querySelector("#detail-title"),
  detailTask: document.querySelector("#detail-task"),
  detailFailureCount: document.querySelector("#detail-failure-count"),
  detailFirstSeen: document.querySelector("#detail-first-seen"),
  detailLastSeen: document.querySelector("#detail-last-seen"),
  detailIncidentId: document.querySelector("#detail-incident-id"),
  detailSignatureLink: document.querySelector("#detail-signature-link"),
  detailFailures: document.querySelector("#detail-failures"),
  transitionHistory: document.querySelector("#transition-history"),
  transitionCount: document.querySelector("#transition-count"),
  transitionEmpty: document.querySelector("#transition-empty"),
  operatorControls: document.querySelector("#operator-controls"),
  operatorHelp: document.querySelector("#operator-help"),
  operatorActions: document.querySelector("#operator-actions"),
  viewerStateNote: document.querySelector("#viewer-state-note"),
  humanDiagnosisContent: document.querySelector("#human-diagnosis-content"),
  humanDiagnosisRevision: document.querySelector("#human-diagnosis-revision"),
  humanDiagnosisActions: document.querySelector("#human-diagnosis-actions"),
  humanDiagnosisHistory: document.querySelector("#human-diagnosis-history"),
  humanDiagnosisDialog: document.querySelector("#human-diagnosis-dialog"),
  humanDiagnosisForm: document.querySelector("#human-diagnosis-form"),
  humanDiagnosisClassification: document.querySelector("#human-diagnosis-classification"),
  humanDiagnosisRootCause: document.querySelector("#human-diagnosis-root-cause"),
  humanDiagnosisActionsInput: document.querySelector("#human-diagnosis-actions-input"),
  humanDiagnosisRetry: document.querySelector("#human-diagnosis-retry"),
  humanDiagnosisNotes: document.querySelector("#human-diagnosis-notes"),
  humanDiagnosisChangeReason: document.querySelector("#human-diagnosis-change-reason"),
  humanDiagnosisError: document.querySelector("#human-diagnosis-error"),
  humanDiagnosisClose: document.querySelector("#human-diagnosis-close"),
  humanDiagnosisCancel: document.querySelector("#human-diagnosis-cancel"),
  humanDiagnosisConfirm: document.querySelector("#human-diagnosis-confirm"),
  transitionDialog: document.querySelector("#transition-dialog"),
  transitionForm: document.querySelector("#transition-form"),
  transitionDialogTitle: document.querySelector("#transition-dialog-title"),
  transitionDialogCopy: document.querySelector("#transition-dialog-copy"),
  transitionReason: document.querySelector("#transition-reason"),
  transitionError: document.querySelector("#transition-error"),
  transitionClose: document.querySelector("#transition-close"),
  transitionCancel: document.querySelector("#transition-cancel"),
  transitionConfirm: document.querySelector("#transition-confirm"),
  signatureDashboard: document.querySelector("#signature-dashboard"),
  signatureFilterForm: document.querySelector("#signature-filter-form"),
  clearSignatureFilters: document.querySelector("#clear-signature-filters"),
  signatureTotal: document.querySelector("#signature-total"),
  signatureLoading: document.querySelector("#signature-loading"),
  signatureError: document.querySelector("#signature-dashboard-error"),
  signatureEmpty: document.querySelector("#signature-empty"),
  signatureResults: document.querySelector("#signature-results"),
  signatureRange: document.querySelector("#signature-range"),
  signatureRows: document.querySelector("#signature-rows"),
  signaturePrevious: document.querySelector("#signature-previous-page"),
  signatureNext: document.querySelector("#signature-next-page"),
  signaturePageLabel: document.querySelector("#signature-page-label"),
  signatureDetail: document.querySelector("#signature-detail"),
  signatureDetailBack: document.querySelector("#signature-detail-back"),
  signatureBreadcrumbCurrent: document.querySelector("#signature-breadcrumb-current"),
  signatureDetailLoading: document.querySelector("#signature-detail-loading"),
  signatureDetailError: document.querySelector("#signature-detail-error"),
  signatureDetailContent: document.querySelector("#signature-detail-content"),
  signatureDetailTitle: document.querySelector("#signature-detail-title"),
  signatureDetailMessage: document.querySelector("#signature-detail-message"),
  signatureDetailFailures: document.querySelector("#signature-detail-failures"),
  signatureDetailIncidents: document.querySelector("#signature-detail-incidents"),
  signatureDetailFirstSeen: document.querySelector("#signature-detail-first-seen"),
  signatureDetailLastSeen: document.querySelector("#signature-detail-last-seen"),
  signatureCanonicalFields: document.querySelector("#signature-canonical-fields"),
  signatureLatestDiagnosis: document.querySelector("#signature-latest-diagnosis"),
  signatureOperatorDiagnoses: document.querySelector("#signature-operator-diagnoses"),
  signatureTrend: document.querySelector("#signature-trend"),
  signatureTrendRange: document.querySelector("#signature-trend-range"),
  signatureTrend7: document.querySelector("#signature-trend-7"),
  signatureTrend30: document.querySelector("#signature-trend-30"),
  signatureOccurrenceRows: document.querySelector("#signature-occurrence-rows"),
  signatureOccurrenceRange: document.querySelector("#signature-occurrence-range"),
  signatureOccurrenceEmpty: document.querySelector("#signature-occurrence-empty"),
  signatureOccurrenceTable: document.querySelector("#signature-occurrence-table"),
  occurrencePrevious: document.querySelector("#occurrence-previous-page"),
  occurrenceNext: document.querySelector("#occurrence-next-page"),
  occurrencePageLabel: document.querySelector("#occurrence-page-label"),
  diagnosisDashboard: document.querySelector("#diagnosis-dashboard"),
  diagnosisFilterForm: document.querySelector("#diagnosis-filter-form"),
  clearDiagnosisFilters: document.querySelector("#clear-diagnosis-filters"),
  diagnosisTotal: document.querySelector("#diagnosis-total"),
  diagnosisPassedTotal: document.querySelector("#diagnosis-passed-total"),
  diagnosisReusedTotal: document.querySelector("#diagnosis-reused-total"),
  diagnosisRejectedTotal: document.querySelector("#diagnosis-rejected-total"),
  diagnosisLoading: document.querySelector("#diagnosis-loading"),
  diagnosisError: document.querySelector("#diagnosis-dashboard-error"),
  diagnosisEmpty: document.querySelector("#diagnosis-empty"),
  diagnosisResults: document.querySelector("#diagnosis-results"),
  diagnosisRange: document.querySelector("#diagnosis-range"),
  diagnosisRows: document.querySelector("#diagnosis-rows"),
  diagnosisPrevious: document.querySelector("#diagnosis-previous-page"),
  diagnosisNext: document.querySelector("#diagnosis-next-page"),
  diagnosisPageLabel: document.querySelector("#diagnosis-page-label"),
  diagnosisDetail: document.querySelector("#diagnosis-detail"),
  diagnosisDetailBack: document.querySelector("#diagnosis-detail-back"),
  diagnosisBreadcrumbCurrent: document.querySelector("#diagnosis-breadcrumb-current"),
  diagnosisDetailLoading: document.querySelector("#diagnosis-detail-loading"),
  diagnosisDetailError: document.querySelector("#diagnosis-detail-error"),
  diagnosisDetailContent: document.querySelector("#diagnosis-detail-content"),
  diagnosisDetailLabels: document.querySelector("#diagnosis-detail-labels"),
  diagnosisDetailTitle: document.querySelector("#diagnosis-detail-title"),
  diagnosisDetailRootCause: document.querySelector("#diagnosis-detail-root-cause"),
  diagnosisDetailSource: document.querySelector("#diagnosis-detail-source"),
  diagnosisDetailConfidence: document.querySelector("#diagnosis-detail-confidence"),
  diagnosisDetailRetry: document.querySelector("#diagnosis-detail-retry"),
  diagnosisDetailReview: document.querySelector("#diagnosis-detail-review"),
  diagnosisDetailCreated: document.querySelector("#diagnosis-detail-created"),
  diagnosisDetailCard: document.querySelector("#diagnosis-detail-card"),
  diagnosisLogPanel: document.querySelector("#diagnosis-log-panel"),
  diagnosisSimilarList: document.querySelector("#diagnosis-similar-list"),
  diagnosisFailureFields: document.querySelector("#diagnosis-failure-fields"),
  diagnosisContextLinks: document.querySelector("#diagnosis-context-links"),
  diagnosisProvenanceFields: document.querySelector("#diagnosis-provenance-fields"),
  diagnosisTechnicalFields: document.querySelector("#diagnosis-technical-fields"),
  diagnosisLinkedIncident: document.querySelector("#diagnosis-linked-incident"),
  diagnosisSignatureLink: document.querySelector("#diagnosis-signature-link"),
  diagnosisRelatedDiagnoses: document.querySelector("#diagnosis-related-diagnoses"),
  diagnosisRelationshipLinks: document.querySelector("#diagnosis-relationship-links"),
  reportDashboard: document.querySelector("#report-dashboard"),
  reportSchedulePanel: document.querySelector("#report-schedule-panel"),
  reportSchedulerStatus: document.querySelector("#report-scheduler-status"),
  reportScheduleError: document.querySelector("#report-schedule-error"),
  reportScheduleList: document.querySelector("#report-schedule-list"),
  reportScheduleForm: document.querySelector("#report-schedule-form"),
  reportScheduleId: document.querySelector("#report-schedule-id"),
  reportScheduleRevision: document.querySelector("#report-schedule-revision"),
  reportScheduleName: document.querySelector("#report-schedule-name"),
  reportScheduleTitle: document.querySelector("#report-schedule-title-input"),
  reportScheduleEnvironment: document.querySelector("#report-schedule-environment"),
  reportScheduleNotificationConnection: document.querySelector("#report-schedule-notification-connection"),
  reportScheduleTime: document.querySelector("#report-schedule-time"),
  reportScheduleTimezone: document.querySelector("#report-schedule-timezone"),
  reportScheduleAiSummary: document.querySelector("#report-schedule-ai-summary"),
  reportScheduleEnabled: document.querySelector("#report-schedule-enabled"),
  reportScheduleSave: document.querySelector("#report-schedule-save"),
  reportSchedulePreview: document.querySelector("#report-schedule-preview"),
  reportManualRunForm: document.querySelector("#report-manual-run-form"),
  reportManualDate: document.querySelector("#report-manual-date"),
  reportManualRun: document.querySelector("#report-manual-run"),
  reportScheduleFeedback: document.querySelector("#report-schedule-feedback"),
  reportScheduleRuns: document.querySelector("#report-schedule-runs"),
  reportFilterForm: document.querySelector("#report-filter-form"),
  clearReportFilters: document.querySelector("#clear-report-filters"),
  reportTotal: document.querySelector("#report-total"),
  reportDeliveredTotal: document.querySelector("#report-delivered-total"),
  reportFailedTotal: document.querySelector("#report-failed-total"),
  reportAiTotal: document.querySelector("#report-ai-total"),
  reportLoading: document.querySelector("#report-loading"),
  reportError: document.querySelector("#report-dashboard-error"),
  reportEmpty: document.querySelector("#report-empty"),
  reportResults: document.querySelector("#report-results"),
  reportRange: document.querySelector("#report-range"),
  reportRows: document.querySelector("#report-rows"),
  reportPrevious: document.querySelector("#report-previous-page"),
  reportNext: document.querySelector("#report-next-page"),
  reportPageLabel: document.querySelector("#report-page-label"),
  reportDetail: document.querySelector("#report-detail"),
  reportDetailBack: document.querySelector("#report-detail-back"),
  reportDetailLoading: document.querySelector("#report-detail-loading"),
  reportDetailError: document.querySelector("#report-detail-error"),
  reportDetailContent: document.querySelector("#report-detail-content"),
  reportDetailLabels: document.querySelector("#report-detail-labels"),
  reportDetailTitle: document.querySelector("#report-detail-title"),
  reportDetailSubtitle: document.querySelector("#report-detail-subtitle"),
  reportDetailFailures: document.querySelector("#report-detail-failures"),
  reportDetailTasks: document.querySelector("#report-detail-tasks"),
  reportDetailDagRuns: document.querySelector("#report-detail-dag-runs"),
  reportDetailUnresolved: document.querySelector("#report-detail-unresolved"),
  reportDetailOverview: document.querySelector("#report-detail-overview"),
  reportDetailHighlights: document.querySelector("#report-detail-highlights"),
  reportDetailPriorities: document.querySelector("#report-detail-priorities"),
  reportDetailAi: document.querySelector("#report-detail-ai"),
  reportDetailAiProvider: document.querySelector("#report-detail-ai-provider"),
  reportDetailAiChanges: document.querySelector("#report-detail-ai-changes"),
  reportDetailAiPriorities: document.querySelector("#report-detail-ai-priorities"),
  reportDeliveryFields: document.querySelector("#report-delivery-fields"),
  reportIncidentFields: document.querySelector("#report-incident-fields"),
  reportClassificationFields: document.querySelector("#report-classification-fields"),
  adminDashboard: document.querySelector("#admin-dashboard"),
  adminCreateForm: document.querySelector("#admin-create-form"),
  adminUserTotal: document.querySelector("#admin-user-total"),
  adminConnectionTotal: document.querySelector("#admin-connection-total"),
  adminFeedback: document.querySelector("#admin-feedback"),
  adminError: document.querySelector("#admin-error"),
  adminLoading: document.querySelector("#admin-loading"),
  adminContent: document.querySelector("#admin-content"),
  adminUserRows: document.querySelector("#admin-user-rows"),
  adminConnectionsEmpty: document.querySelector("#admin-connections-empty"),
  adminConnectionsTable: document.querySelector("#admin-connections-table"),
  adminConnectionRows: document.querySelector("#admin-connection-rows"),
  adminAuditRows: document.querySelector("#admin-audit-rows"),
  connectionForm: document.querySelector("#connection-form"),
  connectionEnvironment: document.querySelector("#connection-environment"),
  connectionProvider: document.querySelector("#connection-provider"),
  connectionDisplayName: document.querySelector("#connection-display-name"),
  connectionApiBaseUrl: document.querySelector("#connection-api-base-url"),
  connectionUiUrlGroup: document.querySelector("#connection-ui-url-group"),
  connectionUiUrl: document.querySelector("#connection-ui-url"),
  connectionModelGroup: document.querySelector("#connection-model-group"),
  connectionModel: document.querySelector("#connection-model"),
  connectionChannelGroup: document.querySelector("#connection-channel-group"),
  connectionChannel: document.querySelector("#connection-channel"),
  connectionSecretGroup: document.querySelector("#connection-secret-group"),
  connectionSecretLabel: document.querySelector("#connection-secret-label"),
  connectionSecret: document.querySelector("#connection-secret"),
  connectionSecretHelp: document.querySelector("#connection-secret-help"),
  connectionCancelEdit: document.querySelector("#connection-cancel-edit"),
  connectionSave: document.querySelector("#connection-save"),
  passwordResetDialog: document.querySelector("#password-reset-dialog"),
  passwordResetForm: document.querySelector("#password-reset-form"),
  passwordResetCopy: document.querySelector("#password-reset-copy"),
  passwordResetValue: document.querySelector("#password-reset-value"),
  passwordResetError: document.querySelector("#password-reset-error"),
  passwordResetClose: document.querySelector("#password-reset-close"),
  passwordResetCancel: document.querySelector("#password-reset-cancel"),
};

let currentOffset = 0;
let currentTotal = 0;
let currentIncidentId = null;
let currentIncidentStatus = null;
let pendingOperatorAction = null;
let currentSignatureOffset = 0;
let currentSignatureTotal = 0;
let currentSignatureId = null;
let currentSignature = null;
let currentSignatureTrendDays = 7;
let currentOccurrenceOffset = 0;
let currentOccurrenceTotal = 0;
let currentDiagnosisOffset = 0;
let currentDiagnosisTotal = 0;
let currentDiagnosisId = null;
let currentReportOffset = 0;
let currentReportTotal = 0;
let currentReportId = null;
let currentReportSchedule = null;
let reportNotificationConnections = [];
let currentUser = null;
let passwordResetUser = null;
let editingConnection = null;
let sidebarCollapsed = window.matchMedia("(max-width: 680px)").matches;

function applySidebarState() {
  const navigationVisible = !elements.sidebarToggle.hidden;
  const toggleLabel = sidebarCollapsed ? "Expand navigation" : "Collapse navigation";
  document.body.classList.toggle("sidebar-hidden", !navigationVisible);
  document.body.classList.toggle("sidebar-collapsed", navigationVisible && sidebarCollapsed);
  elements.siteSidebar.hidden = !navigationVisible;
  elements.sidebarToggle.setAttribute("aria-expanded", String(!sidebarCollapsed));
  elements.sidebarToggle.setAttribute("aria-label", translatedText(toggleLabel));
  elements.sidebarToggle.title = translatedText(toggleLabel);
  elements.sidebarToggleIcon.textContent = sidebarCollapsed ? ">" : "<";
}

function setSidebarCollapsed(collapsed) {
  sidebarCollapsed = collapsed;
  applySidebarState();
}

function setSidebarVisibility(visible) {
  elements.sidebarToggle.hidden = !visible;
  applySidebarState();
}

function storedSession() {
  return {
    token: currentUser !== null,
    role: currentUser ? currentUser.role.toLowerCase() : null,
    displayName: currentUser ? currentUser.display_name : null,
  };
}

function authHeaders() {
  const csrfToken = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("dagsentry_csrf="));
  if (!csrfToken) {
    return {};
  }
  return { "X-CSRF-Token": decodeURIComponent(csrfToken.split("=").slice(1).join("=")) };
}

function clearSession() {
  currentUser = null;
}

function setAuthLoading(isLoading) {
  elements.authLoading.hidden = !isLoading;
  elements.authSubmit.disabled = isLoading;
  elements.accessEmail.disabled = isLoading;
  elements.accessPassword.disabled = isLoading;
  elements.authPanel.setAttribute("aria-busy", String(isLoading));
}

function showAuth(message = "") {
  setAuthLoading(false);
  setSidebarVisibility(false);
  if (elements.transitionDialog.open) {
    elements.transitionDialog.close();
  }
  if (elements.passwordResetDialog.open) {
    elements.passwordResetDialog.close();
  }
  pendingOperatorAction = null;
  passwordResetUser = null;
  resetConnectionForm();
  elements.dashboard.hidden = true;
  elements.detail.hidden = true;
  elements.signatureDashboard.hidden = true;
  elements.signatureDetail.hidden = true;
  elements.diagnosisDashboard.hidden = true;
  elements.diagnosisDetail.hidden = true;
  elements.reportDashboard.hidden = true;
  elements.reportDetail.hidden = true;
  elements.adminDashboard.hidden = true;
  elements.adminNav.hidden = true;
  elements.authPanel.hidden = false;
  elements.authForm.hidden = false;
  elements.changePasswordForm.hidden = true;
  elements.currentPassword.value = "";
  elements.newPassword.value = "";
  elements.confirmNewPassword.value = "";
  elements.changePasswordError.hidden = true;
  document.title = translatedText("DagSentry — Connect");
  elements.disconnect.hidden = true;
  elements.sessionStatus.textContent = "Not connected";
  elements.authError.textContent = message;
  elements.authError.hidden = !message;
  if (message) {
    elements.accessEmail.focus();
  }
}

function showPasswordChange() {
  if (!currentUser) {
    showAuth();
    return;
  }
  setSidebarVisibility(false);
  elements.dashboard.hidden = true;
  elements.detail.hidden = true;
  elements.signatureDashboard.hidden = true;
  elements.signatureDetail.hidden = true;
  elements.diagnosisDashboard.hidden = true;
  elements.diagnosisDetail.hidden = true;
  elements.reportDashboard.hidden = true;
  elements.reportDetail.hidden = true;
  elements.adminDashboard.hidden = true;
  elements.adminNav.hidden = true;
  elements.authPanel.hidden = false;
  elements.authForm.hidden = true;
  elements.changePasswordForm.hidden = false;
  elements.changePasswordError.hidden = true;
  elements.disconnect.hidden = false;
  elements.sessionStatus.textContent = `${currentUser.display_name} · ${translatedText("Password change required")}`;
  document.title = translatedText("DagSentry — Change temporary password");
  elements.currentPassword.focus();
}

function showConnectedView(view) {
  const session = storedSession();
  setSidebarVisibility(true);
  const adminView = view === "admin";
  const signatureView = view === "signatures" || view === "signature-detail";
  const diagnosisView = view === "diagnoses" || view === "diagnosis-detail";
  const reportView = view === "reports" || view === "report-detail";
  const incidentView = !signatureView && !diagnosisView && !reportView && !adminView;
  const titles = {
    dashboard: "DagSentry — Incident response",
    detail: "DagSentry — Incident detail",
    signatures: "DagSentry — Error patterns",
    "signature-detail": "DagSentry — Error Signature detail",
    diagnoses: "DagSentry — Diagnosis History",
    "diagnosis-detail": "DagSentry — Diagnosis detail",
    reports: "DagSentry — Daily Reports",
    "report-detail": "DagSentry — Daily Report detail",
    admin: "DagSentry — Settings",
  };
  document.title = translatedText(titles[view] || "DagSentry");
  elements.authPanel.hidden = true;
  elements.dashboard.hidden = view !== "dashboard";
  elements.detail.hidden = view !== "detail";
  elements.signatureDashboard.hidden = view !== "signatures";
  elements.signatureDetail.hidden = view !== "signature-detail";
  elements.diagnosisDashboard.hidden = view !== "diagnoses";
  elements.diagnosisDetail.hidden = view !== "diagnosis-detail";
  elements.reportDashboard.hidden = view !== "reports";
  elements.reportDetail.hidden = view !== "report-detail";
  elements.adminDashboard.hidden = !adminView;
  elements.adminNav.hidden = session.role !== "admin";
  elements.incidentsNav.classList.toggle("is-active", incidentView);
  elements.signaturesNav.classList.toggle("is-active", signatureView);
  elements.diagnosesNav.classList.toggle("is-active", diagnosisView);
  elements.reportsNav.classList.toggle("is-active", reportView);
  elements.adminNav.classList.toggle("is-active", adminView);
  for (const [nav, active] of [
    [elements.incidentsNav, incidentView],
    [elements.signaturesNav, signatureView],
    [elements.diagnosesNav, diagnosisView],
    [elements.reportsNav, reportView],
    [elements.adminNav, adminView],
  ]) {
    if (active) {
      nav.setAttribute("aria-current", "page");
    } else {
      nav.removeAttribute("aria-current");
    }
  }
  elements.disconnect.hidden = false;
  elements.sessionStatus.textContent = `${session.displayName} · ${session.role}`;
}

function setLoading(isLoading) {
  elements.loading.hidden = !isLoading;
  elements.filterForm.setAttribute("aria-busy", String(isLoading));
  elements.refreshIncidents.disabled = isLoading;
  elements.previous.disabled = isLoading || currentOffset === 0;
  elements.next.disabled = isLoading || currentOffset + PAGE_SIZE >= currentTotal;
}

function revealAdvancedFilters(form) {
  const details = form.querySelector(".advanced-filters");
  details.open = Array.from(details.querySelectorAll("input, select")).some((control) => {
    const defaultValue = control.tagName === "SELECT"
      ? control.options[0].value
      : control.defaultValue;
    return control.value !== defaultValue;
  });
}

function setFiltersFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const defaults = { status: "OPEN", sort: "last_failure_at", order: "desc" };
  for (const control of elements.filterForm.elements) {
    if (!control.name) {
      continue;
    }
    control.value = params.get(control.name) ?? defaults[control.name] ?? "";
  }
  revealAdvancedFilters(elements.filterForm);
  currentOffset = Number.parseInt(params.get("offset") || "0", 10);
  if (!Number.isFinite(currentOffset) || currentOffset < 0) {
    currentOffset = 0;
  }
}

function setSignatureFiltersFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const defaults = { sort: "last_seen_at", order: "desc" };
  for (const control of elements.signatureFilterForm.elements) {
    if (!control.name) {
      continue;
    }
    control.value = params.get(control.name) ?? defaults[control.name] ?? "";
  }
  revealAdvancedFilters(elements.signatureFilterForm);
  currentSignatureOffset = Number.parseInt(params.get("offset") || "0", 10);
  if (!Number.isFinite(currentSignatureOffset) || currentSignatureOffset < 0) {
    currentSignatureOffset = 0;
  }
  currentOccurrenceOffset = Number.parseInt(params.get("occurrence_offset") || "0", 10);
  if (!Number.isFinite(currentOccurrenceOffset) || currentOccurrenceOffset < 0) {
    currentOccurrenceOffset = 0;
  }
}

function setDiagnosisFiltersFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const defaults = { sort: "created_at", order: "desc" };
  for (const control of elements.diagnosisFilterForm.elements) {
    if (!control.name) {
      continue;
    }
    control.value = params.get(control.name) ?? defaults[control.name] ?? "";
  }
  currentDiagnosisOffset = Number.parseInt(params.get("offset") || "0", 10);
  if (!Number.isFinite(currentDiagnosisOffset) || currentDiagnosisOffset < 0) {
    currentDiagnosisOffset = 0;
  }
}

function setReportFiltersFromUrl() {
  const params = new URLSearchParams(window.location.search);
  for (const control of elements.reportFilterForm.elements) {
    if (control.name) {
      control.value = params.get(control.name) ?? "";
    }
  }
  currentReportOffset = Number.parseInt(params.get("offset") || "0", 10);
  if (!Number.isFinite(currentReportOffset) || currentReportOffset < 0) {
    currentReportOffset = 0;
  }
}

function queryFromFilters(offset = currentOffset) {
  const params = new URLSearchParams();
  const data = new FormData(elements.filterForm);
  for (const [name, rawValue] of data.entries()) {
    const value = String(rawValue).trim();
    if (value) {
      params.set(name, value);
    }
  }
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(offset));
  return params;
}

function updateUrl(params) {
  const visible = new URLSearchParams(params);
  visible.delete("limit");
  if (visible.get("offset") === "0") {
    visible.delete("offset");
  }
  const query = visible.toString();
  history.replaceState({}, "", query ? `/ui/?${query}` : "/ui/");
}

function signatureQueryFromFilters(offset = currentSignatureOffset) {
  const params = new URLSearchParams();
  const data = new FormData(elements.signatureFilterForm);
  for (const [name, rawValue] of data.entries()) {
    const value = String(rawValue).trim();
    if (value) {
      params.set(name, value);
    }
  }
  params.set("limit", String(SIGNATURE_PAGE_SIZE));
  params.set("offset", String(offset));
  return params;
}

function updateSignatureUrl(params) {
  const visible = new URLSearchParams(params);
  visible.set("view", "signatures");
  visible.delete("limit");
  if (visible.get("offset") === "0") {
    visible.delete("offset");
  }
  history.replaceState({}, "", `/ui/?${visible.toString()}`);
}

function signatureDetailBackHref() {
  const params = new URLSearchParams(window.location.search);
  params.set("view", "signatures");
  params.delete("signature");
  params.delete("occurrence_offset");
  return `/ui/?${params.toString()}`;
}

function occurrenceQueryFromUrl(offset = currentOccurrenceOffset) {
  const source = new URLSearchParams(window.location.search);
  const params = new URLSearchParams();
  for (const name of ["environment", "dag_id", "task_id", "date_from", "date_to"]) {
    if (source.has(name)) {
      params.set(name, source.get(name));
    }
  }
  params.set("limit", String(OCCURRENCE_PAGE_SIZE));
  params.set("offset", String(offset));
  return params;
}

function diagnosisQueryFromFilters(offset = currentDiagnosisOffset) {
  const params = new URLSearchParams();
  const data = new FormData(elements.diagnosisFilterForm);
  for (const [name, rawValue] of data.entries()) {
    if (!["source_type", "error_signature_id", "date_from", "date_to"].includes(name)) {
      continue;
    }
    const value = String(rawValue).trim();
    if (value) {
      params.set(name, value);
    }
  }
  params.set("limit", String(DIAGNOSIS_PAGE_SIZE));
  params.set("offset", String(offset));
  return params;
}

function updateDiagnosisUrl(params) {
  const visible = new URLSearchParams(params);
  visible.set("view", "diagnoses");
  visible.delete("limit");
  if (visible.get("offset") === "0") {
    visible.delete("offset");
  }
  history.replaceState({}, "", `/ui/?${visible.toString()}`);
}

function diagnosisDetailBackHref() {
  const params = new URLSearchParams(window.location.search);
  params.set("view", "diagnoses");
  params.delete("diagnosis");
  return `/ui/?${params.toString()}`;
}

function reportQueryFromFilters(offset = currentReportOffset) {
  const params = new URLSearchParams();
  const data = new FormData(elements.reportFilterForm);
  for (const [name, rawValue] of data.entries()) {
    const value = String(rawValue).trim();
    if (value) {
      params.set(name, value);
    }
  }
  params.set("limit", String(REPORT_PAGE_SIZE));
  params.set("offset", String(offset));
  return params;
}

function updateReportUrl(params) {
  const visible = new URLSearchParams(params);
  visible.set("view", "reports");
  visible.delete("limit");
  if (visible.get("offset") === "0") {
    visible.delete("offset");
  }
  history.replaceState({}, "", `/ui/?${visible.toString()}`);
}

function reportDetailBackHref() {
  const params = new URLSearchParams(window.location.search);
  params.set("view", "reports");
  params.delete("report");
  return `/ui/?${params.toString()}`;
}

function appendCell(row, content, className = "") {
  const cell = document.createElement("td");
  if (className) {
    cell.className = className;
  }
  if (content instanceof Node) {
    cell.append(content);
  } else {
    cell.textContent = content;
  }
  row.append(cell);
}

function shortId(value) {
  return String(value).slice(0, 8).toUpperCase();
}

function formatTimestamp(value) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return { primary: "Unavailable" };
  }
  const selectedTimezone = currentTimezone === "browser"
    ? Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
    : currentTimezone;
  const primary = `${formatDateTime(timestamp, selectedTimezone)} ${timezoneLabel(timestamp, selectedTimezone)}`;
  return { primary };
}

function formatDateTime(timestamp, timeZone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).formatToParts(timestamp);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
}

function timezoneLabel(timestamp, timeZone) {
  if (timeZone === "UTC") {
    return "UTC";
  }
  if (timeZone === "Asia/Seoul") {
    return "KST";
  }
  const name = new Intl.DateTimeFormat("en", {
    timeZone,
    timeZoneName: "short",
  }).formatToParts(timestamp).find((part) => part.type === "timeZoneName");
  return name?.value || timeZone;
}

function timestampBlock(value) {
  const formatted = formatTimestamp(value);
  const wrapper = document.createElement("div");
  const primary = document.createElement("div");
  primary.textContent = formatted.primary;
  wrapper.append(primary);
  return wrapper;
}

function safeHttpUrl(value) {
  try {
    const url = new URL(value, window.location.origin);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function renderRows(items) {
  elements.rows.replaceChildren();
  for (const incident of items) {
    const row = document.createElement("tr");
    row.dataset.incidentId = incident.id;

    const status = document.createElement("span");
    status.className = "status-badge";
    status.dataset.status = incident.status;
    status.textContent = incident.status.replaceAll("_", " ");
    appendCell(row, status);

    const incidentIdentity = document.createElement("div");
    incidentIdentity.className = "task-identity incident-error-summary";
    incidentIdentity.append(
      textElement("strong", incident.exception_class || incident.normalized_message || "Error summary unavailable"),
    );
    if (incident.exception_class && incident.normalized_message && incident.normalized_message !== incident.exception_class) {
      incidentIdentity.append(textElement("span", incident.normalized_message));
    }
    appendCell(row, incidentIdentity);
    appendCell(row, incident.environment);

    const taskIdentity = document.createElement("div");
    taskIdentity.className = "task-identity";
    taskIdentity.append(
      textElement("strong", incident.dag_id),
      textElement("span", incident.task_id),
    );
    appendCell(row, taskIdentity);

    const failures = document.createElement("span");
    failures.className = "failure-count";
    failures.textContent = String(incident.failure_count);
    appendCell(row, failures, "numeric");
    appendCell(row, timestampBlock(incident.last_failure_at));

    const inspect = document.createElement("a");
    const detailParams = new URLSearchParams(window.location.search);
    detailParams.set("incident", incident.id);
    inspect.className = "inspect-link";
    inspect.href = `/ui/?${detailParams.toString()}`;
    inspect.textContent = "Inspect";
    inspect.setAttribute("aria-label", `Inspect ${incident.dag_id} ${incident.task_id} Incident`);
    appendCell(row, inspect, "action-cell");
    elements.rows.append(row);
  }
}

function renderPage(payload) {
  currentTotal = payload.total;
  elements.total.textContent = String(payload.total);
  elements.error.hidden = true;
  elements.empty.hidden = payload.items.length !== 0;
  elements.results.hidden = payload.items.length === 0;
  renderRows(payload.items);

  const start = payload.items.length ? currentOffset + 1 : 0;
  const end = currentOffset + payload.items.length;
  elements.range.textContent = `${start}–${end} of ${payload.total}`;
  const page = Math.floor(currentOffset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(payload.total / PAGE_SIZE));
  elements.pageLabel.textContent = `Page ${page} of ${pages}`;
  elements.previous.disabled = currentOffset === 0;
  elements.next.disabled = currentOffset + PAGE_SIZE >= payload.total;
}

async function loadIncidentSummary(submittedParams) {
  const totals = await Promise.all(["OPEN", "ACKNOWLEDGED"].map(async (status) => {
    const params = new URLSearchParams(submittedParams);
    params.set("status", status);
    params.set("limit", "1");
    params.set("offset", "0");
    const response = await fetch(`/api/v1/incidents?${params.toString()}`, {
      headers: authHeaders(),
    });
    return response.ok ? (await response.json()).total : null;
  }));
  elements.openTotal.textContent = totals[0] === null ? "—" : String(totals[0]);
  elements.acknowledgedTotal.textContent = totals[1] === null ? "—" : String(totals[1]);
}

function setSignatureLoading(isLoading) {
  elements.signatureLoading.hidden = !isLoading;
  elements.signatureFilterForm.setAttribute("aria-busy", String(isLoading));
  elements.signaturePrevious.disabled = isLoading || currentSignatureOffset === 0;
  elements.signatureNext.disabled = isLoading
    || currentSignatureOffset + SIGNATURE_PAGE_SIZE >= currentSignatureTotal;
}

function signatureLabel(signature) {
  return signature.exception_class
    || signature.vendor_error_code
    || signature.operator_type
    || "Unclassified Error Signature";
}

function renderSignatureRows(items) {
  elements.signatureRows.replaceChildren();
  for (const signature of items) {
    const row = document.createElement("tr");
    row.dataset.signatureId = signature.id;

    const identity = document.createElement("div");
    identity.className = "signature-identity";
    identity.append(
      textElement("strong", signatureLabel(signature)),
      textElement("span", signature.normalized_message || "No normalized message"),
    );
    appendCell(row, identity);

    const correlation = document.createElement("div");
    correlation.className = "task-identity";
    correlation.append(
      textElement("strong", signature.operator_type || "Unknown operator"),
      textElement(
        "span",
        [signature.exception_class, signature.vendor_error_code].filter(Boolean).join(" · ")
          || "No exception or vendor code",
      ),
    );
    appendCell(row, correlation);
    appendCell(row, String(signature.incident_count), "numeric");
    appendCell(row, String(signature.failure_count), "numeric");
    appendCell(row, timestampBlock(signature.first_seen_at));
    appendCell(row, timestampBlock(signature.last_seen_at));

    const inspect = document.createElement("a");
    const params = new URLSearchParams(window.location.search);
    params.set("view", "signatures");
    params.set("signature", signature.id);
    params.delete("occurrence_offset");
    inspect.className = "inspect-link";
    inspect.href = `/ui/?${params.toString()}`;
    inspect.textContent = "Explore";
    inspect.setAttribute("aria-label", `Explore ${signatureLabel(signature)}`);
    appendCell(row, inspect, "action-cell");
    elements.signatureRows.append(row);
  }
}

function renderSignaturePage(payload) {
  currentSignatureTotal = payload.total;
  elements.signatureTotal.textContent = String(payload.total);
  elements.signatureError.hidden = true;
  elements.signatureEmpty.hidden = payload.items.length !== 0;
  elements.signatureResults.hidden = payload.items.length === 0;
  renderSignatureRows(payload.items);

  const start = payload.items.length ? currentSignatureOffset + 1 : 0;
  const end = currentSignatureOffset + payload.items.length;
  elements.signatureRange.textContent = `${start}–${end} of ${payload.total}`;
  const page = Math.floor(currentSignatureOffset / SIGNATURE_PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(payload.total / SIGNATURE_PAGE_SIZE));
  elements.signaturePageLabel.textContent = `Page ${page} of ${pages}`;
  elements.signaturePrevious.disabled = currentSignatureOffset === 0;
  elements.signatureNext.disabled = currentSignatureOffset + SIGNATURE_PAGE_SIZE >= payload.total;
}

function renderLatestSignatureDiagnosis(diagnosis) {
  elements.signatureLatestDiagnosis.replaceChildren();
  if (!diagnosis) {
    elements.signatureLatestDiagnosis.append(textElement(
      "p",
      "No validated original Diagnosis is available.",
      "aside-empty",
    ));
    return;
  }

  const labels = document.createElement("div");
  labels.className = "diagnosis-labels signature-diagnosis-labels";
  labels.append(
    sourceBadge(diagnosis.source),
    textElement("span", diagnosis.classification, "classification-badge"),
  );
  const rootCause = textElement(
    "p",
    diagnosis.root_cause || "No Root Cause was produced.",
    "signature-root-cause",
  );
  const facts = document.createElement("dl");
  facts.className = "signature-diagnosis-facts";
  facts.append(
    definitionItem("Confidence", `${Math.round(diagnosis.confidence * 100)}%`),
    definitionItem("Retry", diagnosis.retry_decision),
    definitionItem("Created", formatTimestamp(diagnosis.created_at).primary),
  );
  elements.signatureLatestDiagnosis.append(
    labels,
    rootCause,
    facts,
    contextLink("Inspect Diagnosis", diagnosisHref(diagnosis.id)),
  );
}

function renderSignatureIdentity(signature) {
  elements.signatureBreadcrumbCurrent.textContent = signatureLabel(signature);
  elements.signatureDetailTitle.textContent = signatureLabel(signature);
  elements.signatureDetailMessage.textContent = signature.normalized_message
    || "No normalized message was retained.";
  elements.signatureDetailFailures.textContent = String(signature.failure_count);
  elements.signatureDetailIncidents.textContent = String(signature.incident_count);
  elements.signatureDetailFirstSeen.textContent = formatTimestamp(signature.first_seen_at).primary;
  elements.signatureDetailLastSeen.textContent = formatTimestamp(signature.last_seen_at).primary;
  elements.signatureCanonicalFields.replaceChildren(
    copyDefinitionItem("Fingerprint", signature.fingerprint),
    definitionItem("Fingerprint version", String(signature.fingerprint_version)),
    definitionItem("Operator", signature.operator_type || "Unavailable"),
    definitionItem("Exception", signature.exception_class || "Unavailable"),
    definitionItem("Vendor code", signature.vendor_error_code || "Unavailable"),
    definitionItem("Stack frame", signature.application_stack_frame || "Unavailable"),
  );
  renderLatestSignatureDiagnosis(signature.latest_validated_diagnosis);
  const operatorDiagnoses = signature.operator_diagnoses || [];
  elements.signatureOperatorDiagnoses.replaceChildren();
  if (!operatorDiagnoses.length) {
    elements.signatureOperatorDiagnoses.append(textElement("p", "등록된 과거 운영자 진단이 없습니다.", "aside-empty"));
    return;
  }
  for (const diagnosis of operatorDiagnoses) {
    const item = document.createElement("article");
    item.className = "diagnosis-card operator-diagnosis-card";
    const meta = document.createElement("div");
    meta.className = "diagnosis-card-meta";
    const metaIncident = document.createElement("div");
    const incidentLink = contextLink(
      `INC-${shortId(diagnosis.incident_id)}`,
      `/ui/?incident=${encodeURIComponent(diagnosis.incident_id)}`,
    );
    incidentLink.className = "operator-diagnosis-id";
    metaIncident.append(incidentLink);
    const metaAuthor = document.createElement("div");
    metaAuthor.append(
      textElement("time", formatTimestamp(diagnosis.created_at).primary),
      textElement("span", diagnosis.actor_identity, "muted-label"),
    );
    meta.append(metaIncident, metaAuthor);
    const body = document.createElement("div");
    body.className = "operator-diagnosis-body";
    const fields = document.createElement("dl");
    fields.className = "diagnosis-card-fields";
    fields.append(
      definitionItem("근본 원인", diagnosis.root_cause),
      definitionItem("분류", humanClassificationLabel(diagnosis.classification)),
      definitionItem("재시도 판단", humanRetryLabel(diagnosis.retry_decision)),
    );
    body.append(fields);
    const recommendedActions = diagnosis.recommended_actions || [];
    if (recommendedActions.length) {
      const actionsSection = document.createElement("section");
      actionsSection.className = "operator-diagnosis-recommendations";
      actionsSection.append(textElement("h3", "권장 조치"));
      const actions = document.createElement("ul");
      for (const action of recommendedActions) actions.append(textElement("li", action));
      actionsSection.append(actions);
      body.append(actionsSection);
    }
    const footer = document.createElement("div");
    footer.className = "operator-diagnosis-actions";
    const incidentButton = contextLink(
      "인시던트 보기",
      `/ui/?incident=${encodeURIComponent(diagnosis.incident_id)}`,
    );
    incidentButton.className = "button button-secondary operator-diagnosis-button";
    footer.append(incidentButton);
    item.append(meta, body, footer);
    elements.signatureOperatorDiagnoses.append(item);
  }
}

function addUtcDays(value, days) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function trendQueryForSignature(signature, days = currentSignatureTrendDays) {
  const dateTo = signature.last_seen_at.slice(0, 10);
  const dateFrom = addUtcDays(dateTo, -(days - 1));
  return new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
}

function updateSignatureTrendControls() {
  elements.signatureTrend7.setAttribute("aria-pressed", String(currentSignatureTrendDays === 7));
  elements.signatureTrend30.setAttribute("aria-pressed", String(currentSignatureTrendDays === 30));
}

function renderSignatureTrend(payload) {
  elements.signatureTrend.replaceChildren();
  elements.signatureTrendRange.textContent = `${payload.date_from} – ${payload.date_to}`;
  const maximum = Math.max(0, ...payload.items.map((item) => item.failure_count));
  for (const item of payload.items) {
    const level = item.failure_count === 0
      ? 0
      : Math.max(1, Math.ceil((item.failure_count / maximum) * 10));
    const bucket = document.createElement("li");
    bucket.setAttribute(
      "aria-label",
      `${item.date}: ${item.failure_count} Failure${item.failure_count === 1 ? "" : "s"}`,
    );
    const bar = document.createElement("span");
    bar.className = `trend-bar trend-level-${level}`;
    bar.setAttribute("aria-hidden", "true");
    bucket.append(
      textElement("span", String(item.failure_count), "trend-count"),
      bar,
      textElement("time", item.date.slice(5), "trend-date"),
    );
    elements.signatureTrend.append(bucket);
  }
  requestAnimationFrame(() => {
    elements.signatureTrend.scrollLeft = elements.signatureTrend.scrollWidth;
  });
}

function renderSignatureOccurrences(payload) {
  currentOccurrenceTotal = payload.total;
  elements.signatureOccurrenceRows.replaceChildren();
  elements.signatureOccurrenceEmpty.hidden = payload.items.length !== 0;
  elements.signatureOccurrenceTable.hidden = payload.items.length === 0;
  for (const occurrence of payload.items) {
    const row = document.createElement("tr");
    const incident = document.createElement("div");
    incident.className = "task-identity";
    const status = textElement("strong", occurrence.incident_status);
    status.className = "status-badge";
    status.dataset.status = occurrence.incident_status;
    incident.append(status, textElement("span", `INC-${shortId(occurrence.incident_id)}`));
    appendCell(row, incident);

    const task = document.createElement("div");
    task.className = "task-identity";
    task.append(textElement("strong", occurrence.dag_id), textElement("span", occurrence.task_id));
    appendCell(row, task);
    appendCell(row, occurrence.environment);

    const taskTry = document.createElement("div");
    taskTry.className = "task-identity";
    taskTry.append(
      textElement("strong", `Try ${occurrence.try_number} · ${occurrence.failure_state}`),
      textElement("span", `${occurrence.dag_run_id} · map ${occurrence.map_index}`),
    );
    appendCell(row, taskTry);
    appendCell(row, timestampBlock(occurrence.observed_at));

    const inspect = document.createElement("a");
    inspect.className = "inspect-link";
    inspect.href = `/ui/?incident=${encodeURIComponent(occurrence.incident_id)}`;
    inspect.textContent = "Inspect";
    inspect.setAttribute("aria-label", `Inspect Incident ${occurrence.incident_id}`);
    appendCell(row, inspect, "action-cell");
    elements.signatureOccurrenceRows.append(row);
  }

  const start = payload.items.length ? currentOccurrenceOffset + 1 : 0;
  const end = currentOccurrenceOffset + payload.items.length;
  elements.signatureOccurrenceRange.textContent = `${start}–${end} of ${payload.total}`;
  const page = Math.floor(currentOccurrenceOffset / OCCURRENCE_PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(payload.total / OCCURRENCE_PAGE_SIZE));
  elements.occurrencePageLabel.textContent = `Page ${page} of ${pages}`;
  elements.occurrencePrevious.disabled = currentOccurrenceOffset === 0;
  elements.occurrenceNext.disabled = currentOccurrenceOffset + OCCURRENCE_PAGE_SIZE >= payload.total;
}

function setDiagnosisLoading(isLoading) {
  elements.diagnosisLoading.hidden = !isLoading;
  elements.diagnosisFilterForm.setAttribute("aria-busy", String(isLoading));
  elements.diagnosisPrevious.disabled = isLoading || currentDiagnosisOffset === 0;
  elements.diagnosisNext.disabled = isLoading
    || currentDiagnosisOffset + DIAGNOSIS_PAGE_SIZE >= currentDiagnosisTotal;
}

function sourceBadge(source) {
  const badge = textElement("span", source, "source-badge");
  badge.dataset.source = source;
  return badge;
}

function validationBadge(validationStatus) {
  const badge = textElement("span", validationStatus, "validation-badge");
  badge.dataset.validation = validationStatus;
  if (validationStatus === "REJECTED") {
    badge.classList.add("is-rejected");
  }
  return badge;
}

function diagnosisHistorySourceBadge(sourceType) {
  const badge = sourceBadge(sourceType);
  badge.textContent = {
    AI: "AI",
    RULE: "규칙",
    OPERATOR: "운영자",
  }[sourceType] || sourceType;
  return badge;
}

function diagnosisHistoryStatusBadge(status) {
  const badge = validationBadge(status);
  badge.textContent = {
    PASSED: "검증 통과",
    REJECTED: "거부",
    CONFIRMED: "확정",
    UPDATED: "수정됨",
    WITHDRAWN: "철회",
  }[status] || status;
  if (status === "REJECTED" || status === "WITHDRAWN") {
    badge.classList.add("is-rejected");
  }
  return badge;
}

function diagnosisHref(diagnosisId) {
  const params = new URLSearchParams(window.location.search);
  params.delete("incident");
  params.delete("signature");
  params.delete("occurrence_offset");
  params.set("view", "diagnoses");
  params.set("diagnosis", diagnosisId);
  return `/ui/?${params.toString()}`;
}

function renderDiagnosisRows(items) {
  elements.diagnosisRows.replaceChildren();
  for (const diagnosis of items) {
    const row = document.createElement("tr");
    if (diagnosis.incident_id) {
      appendCell(
        row,
        contextLink(
          `INC-${shortId(diagnosis.incident_id)}`,
          `/ui/?incident=${encodeURIComponent(diagnosis.incident_id)}`,
        ),
      );
    } else {
      appendCell(row, textElement("span", "—"));
    }
    if (diagnosis.error_signature_id) {
      appendCell(row, contextLink(`SIG-${shortId(diagnosis.error_signature_id)}`, `/ui/?view=signatures&signature=${encodeURIComponent(diagnosis.error_signature_id)}`));
    } else {
      appendCell(row, textElement("span", "UNSIGNABLE", "muted-badge"));
    }
    appendCell(row, diagnosisHistorySourceBadge(diagnosis.source_type));
    appendCell(row, textElement("span", diagnosis.root_cause || "—"));
    appendCell(row, diagnosisHistoryStatusBadge(diagnosis.status));
    appendCell(row, diagnosis.actor_identity || "—");
    appendCell(row, timestampBlock(diagnosis.created_at));
    elements.diagnosisRows.append(row);
  }
}

function renderDiagnosisPage(payload) {
  currentDiagnosisTotal = payload.total;
  elements.diagnosisError.hidden = true;
  elements.diagnosisEmpty.hidden = payload.items.length !== 0;
  elements.diagnosisResults.hidden = payload.items.length === 0;
  renderDiagnosisRows(payload.items);

  const start = payload.items.length ? currentDiagnosisOffset + 1 : 0;
  const end = currentDiagnosisOffset + payload.items.length;
  elements.diagnosisRange.textContent = `${start}–${end} of ${payload.total}`;
  const page = Math.floor(currentDiagnosisOffset / DIAGNOSIS_PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(payload.total / DIAGNOSIS_PAGE_SIZE));
  elements.diagnosisPageLabel.textContent = `Page ${page} of ${pages}`;
  elements.diagnosisPrevious.disabled = currentDiagnosisOffset === 0;
  elements.diagnosisNext.disabled = currentDiagnosisOffset + DIAGNOSIS_PAGE_SIZE >= payload.total;
}

async function fetchDiagnosisTotal(query = "") {
  const suffix = query ? `&${query}` : "";
  const response = await fetch(`/api/v1/diagnoses/history?limit=1&offset=0${suffix}`, {
    headers: authHeaders(),
  });
  if (!response.ok) {
    return null;
  }
  return (await response.json()).total;
}

async function loadDiagnosisSummary() {
  const summaries = [
    [elements.diagnosisTotal, ""],
    [elements.diagnosisPassedTotal, "source_type=AI"],
    [elements.diagnosisReusedTotal, "source_type=RULE"],
    [elements.diagnosisRejectedTotal, "source_type=OPERATOR"],
  ];
  for (const [element] of summaries) {
    element.textContent = "—";
  }
  const results = await Promise.allSettled(
    summaries.map(([, query]) => fetchDiagnosisTotal(query)),
  );
  for (const [index, result] of results.entries()) {
    const total = result.status === "fulfilled" ? result.value : null;
    summaries[index][0].textContent = total === null ? "—" : String(total);
  }
}

function contextLink(label, href, external = false) {
  const link = document.createElement("a");
  link.className = "inspect-link";
  link.href = href;
  link.textContent = label;
  if (external) {
    link.target = "_blank";
    link.rel = "noreferrer noopener";
  }
  return link;
}

function renderDiagnosisDetail(diagnosis) {
  elements.diagnosisBreadcrumbCurrent.textContent = `DIAG-${shortId(diagnosis.id)}`;
  elements.diagnosisDetailLabels.replaceChildren(
    sourceBadge(diagnosis.source),
    validationBadge(diagnosis.validation_status),
  );
  if (diagnosis.effective) {
    elements.diagnosisDetailLabels.append(textElement("span", "Effective", "effective-badge"));
  }
  elements.diagnosisDetailTitle.textContent = diagnosis.classification || "UNKNOWN";
  elements.diagnosisDetailRootCause.textContent = diagnosis.root_cause
    || "No Root Cause was produced.";
  elements.diagnosisDetailSource.textContent = diagnosis.source;
  elements.diagnosisDetailConfidence.textContent = diagnosis.confidence === null
    ? "Unavailable"
    : `${Math.round(diagnosis.confidence * 100)}%`;
  elements.diagnosisDetailRetry.textContent = diagnosis.retry_decision || "Unavailable";
  elements.diagnosisDetailReview.textContent = diagnosis.operator_review_required === null
    ? "Unavailable"
    : diagnosis.operator_review_required ? "Operator required" : "Not required";
  elements.diagnosisDetailCreated.textContent = formatTimestamp(diagnosis.created_at).primary;

  const rootCause = document.createElement("section");
  rootCause.className = "context-panel diagnosis-primary-section";
  rootCause.append(
    textElement("h2", "Root Cause"),
    textElement(
      "p",
      diagnosis.root_cause || "No Root Cause was produced.",
      "diagnosis-detail-cause",
    ),
  );
  if (diagnosis.validation_status === "REJECTED") {
    rootCause.append(textElement(
      "p",
      "Rejected AI attempt — retained for provenance and never used as the effective Diagnosis.",
      "rejection-note",
    ));
  }
  if (diagnosis.source === "REUSED") {
    rootCause.append(textElement(
      "p",
      `Content resolved from original Diagnosis ${diagnosis.content_diagnosis_id}.`,
      "reuse-note",
    ));
  }

  const evidence = renderEvidence(diagnosis.evidence, "h2");
  evidence.classList.add("context-panel", "diagnosis-primary-section");
  const actions = renderActions(diagnosis.recommended_actions, "h2");
  if (actions) {
    actions.classList.add("context-panel", "diagnosis-primary-section");
  }

  const reasoning = document.createElement("section");
  reasoning.className = "diagnosis-supplement context-panel";
  reasoning.append(textElement("h3", "Reasoning metadata"));
  const reasoningFields = document.createElement("dl");
  reasoningFields.className = "context-fields";
  reasoningFields.append(
    definitionItem("Confidence reason", diagnosis.confidence_reason || "Unavailable"),
    definitionItem("Matched rule", diagnosis.matched_rule || "Unavailable"),
  );
  reasoning.append(reasoningFields);
  reasoning.append(textElement("p", "Confidence is the diagnosis score, not a measured accuracy rate.", "heading-copy"));
  reasoning.append(textElement("p", "Validation describes automated checks, not operator confirmation of the cause. AI checks include matching cited evidence to the log.", "heading-copy"));
  if (diagnosis.extracted_values.length) {
    const extracted = document.createElement("ul");
    extracted.className = "extracted-values";
    for (const value of diagnosis.extracted_values) {
      extracted.append(textElement("li", `${value.name}: ${value.value}`));
    }
    reasoning.append(textElement("h4", "Extracted values"), extracted);
  }
  elements.diagnosisDetailCard.replaceChildren(rootCause, evidence);
  if (actions) {
    elements.diagnosisDetailCard.append(actions);
  }
  if (diagnosis.validation_errors.length) {
    const validation = document.createElement("section");
    validation.className = "context-panel diagnosis-primary-section validation-errors";
    validation.append(textElement("h2", "Validation errors"));
    const list = document.createElement("ul");
    for (const error of diagnosis.validation_errors) {
      list.append(textElement("li", error));
    }
    validation.append(list);
    elements.diagnosisDetailCard.append(validation);
  }
  elements.diagnosisDetailCard.append(reasoning);

  const failure = diagnosis.failure;
  elements.diagnosisFailureFields.replaceChildren(
    definitionItem("Environment", failure.environment),
    definitionItem("DAG", failure.dag_id),
    definitionItem("Task", failure.task_id),
    definitionItem("DAG run", failure.dag_run_id),
    definitionItem("Try", String(failure.try_number)),
    definitionItem("Map index", String(failure.map_index)),
    definitionItem("State", failure.state),
    definitionItem("Observed", formatTimestamp(failure.observed_at).primary),
  );

  elements.diagnosisContextLinks.replaceChildren();
  elements.diagnosisLogPanel.hidden = true;
  const airflowLogUrl = safeHttpUrl(diagnosis.airflow_log_url);
  if (airflowLogUrl) {
    elements.diagnosisContextLinks.append(contextLink(
      "Open Task log in Airflow ↗",
      airflowLogUrl,
      true,
    ));
    elements.diagnosisLogPanel.hidden = false;
  }

  elements.diagnosisProvenanceFields.replaceChildren(
    copyDefinitionItem("Diagnosis ID", diagnosis.id),
    copyDefinitionItem("Failure Event ID", diagnosis.failure_event_id),
    ...(diagnosis.error_signature_id
      ? [copyDefinitionItem("Signature ID", diagnosis.error_signature_id)]
      : [definitionItem("Signature ID", "Unavailable")]),
    definitionItem("Created", formatTimestamp(diagnosis.created_at).primary),
  );
  elements.diagnosisTechnicalFields.replaceChildren(
    copyDefinitionItem("Content Diagnosis ID", diagnosis.content_diagnosis_id),
    definitionItem("Schema", `v${diagnosis.diagnosis_schema_version}`),
    definitionItem("Prompt", diagnosis.prompt_version || "Unavailable"),
    definitionItem("Rule", diagnosis.rule_version ? `v${diagnosis.rule_version}` : "Unavailable"),
  );

  elements.diagnosisLinkedIncident.replaceChildren();
  if (diagnosis.incident_id) {
    elements.diagnosisLinkedIncident.append(
      textElement("code", `INC-${shortId(diagnosis.incident_id)}`, "linked-incident-id"),
      contextLink(
        "Inspect related Incident",
        `/ui/?incident=${encodeURIComponent(diagnosis.incident_id)}`,
      ),
    );
  } else {
    elements.diagnosisLinkedIncident.append(textElement(
      "p",
      "No linked Incident is available.",
      "aside-empty",
    ));
  }

  elements.diagnosisRelationshipLinks.replaceChildren();
  elements.diagnosisRelatedDiagnoses.hidden = true;
  if (diagnosis.reused_from_diagnosis_id) {
    elements.diagnosisRelationshipLinks.append(contextLink(
      "Open original Diagnosis",
      diagnosisHref(diagnosis.reused_from_diagnosis_id),
    ));
  }
  if (diagnosis.effective_diagnosis_id && diagnosis.effective_diagnosis_id !== diagnosis.id) {
    elements.diagnosisRelationshipLinks.append(contextLink(
      "Open effective Diagnosis",
      diagnosisHref(diagnosis.effective_diagnosis_id),
    ));
  }
  elements.diagnosisRelatedDiagnoses.hidden = !elements.diagnosisRelationshipLinks.childElementCount;

  elements.diagnosisSignatureLink.replaceChildren();
  if (diagnosis.error_signature) {
    elements.diagnosisSignatureLink.append(contextLink(
      "Explore Error Signature",
      `/ui/?view=signatures&signature=${encodeURIComponent(diagnosis.error_signature.id)}`,
    ));
  }
  if (!elements.diagnosisSignatureLink.childElementCount) {
    elements.diagnosisSignatureLink.append(textElement(
      "p",
      "No Error Signature is available.",
      "aside-empty",
    ));
  }
}

function renderSimilarDiagnoses(currentDiagnosisId, items) {
  elements.diagnosisSimilarList.replaceChildren();
  const similar = items.filter((item) => item.id !== currentDiagnosisId).slice(0, 5);
  if (!similar.length) {
    elements.diagnosisSimilarList.append(textElement(
      "p",
      "No other Diagnosis exists for this Error Signature.",
      "aside-empty",
    ));
    return;
  }
  for (const diagnosis of similar) {
    const row = document.createElement("article");
    row.className = "similar-diagnosis-row";
    const identity = document.createElement("div");
    identity.className = "task-identity";
    identity.append(
      textElement("strong", formatTimestamp(diagnosis.created_at).primary),
      textElement("span", `DIAG-${shortId(diagnosis.id)}`),
    );
    const confidence = diagnosis.confidence === null
      ? "Unavailable"
      : `${Math.round(diagnosis.confidence * 100)}%`;
    row.append(
      identity,
      sourceBadge(diagnosis.source),
      textElement("span", confidence, "similar-confidence"),
      validationBadge(diagnosis.validation_status),
      contextLink("View details", diagnosisHref(diagnosis.id)),
    );
    elements.diagnosisSimilarList.append(row);
  }
}

async function loadSimilarDiagnoses(diagnosis) {
  elements.diagnosisSimilarList.replaceChildren(
    textElement("p", "Loading Similar Diagnosis…", "aside-empty"),
  );
  if (!diagnosis.error_signature_id) {
    elements.diagnosisSimilarList.replaceChildren(textElement(
      "p",
      "Similar Diagnosis requires an Error Signature.",
      "aside-empty",
    ));
    return;
  }
  const params = new URLSearchParams({
    error_signature_id: diagnosis.error_signature_id,
    sort: "created_at",
    order: "desc",
    limit: "6",
    offset: "0",
  });
  try {
    const response = await fetch(`/api/v1/diagnoses?${params.toString()}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    renderSimilarDiagnoses(diagnosis.id, (await response.json()).items);
  } catch {
    elements.diagnosisSimilarList.replaceChildren(textElement(
      "p",
      "Unable to load Similar Diagnosis.",
      "aside-empty",
    ));
  }
}

function textElement(tagName, text, className = "") {
  const element = document.createElement(tagName);
  if (className) {
    element.className = className;
  }
  element.textContent = text;
  return element;
}

function definitionItem(term, description) {
  const wrapper = document.createElement("div");
  wrapper.append(textElement("dt", term), textElement("dd", description));
  return wrapper;
}

function copyDefinitionItem(term, description) {
  const wrapper = document.createElement("div");
  wrapper.className = "copy-definition";
  const value = document.createElement("dd");
  value.append(textElement("code", description));
  const button = textElement("button", "Copy", "copy-button");
  button.type = "button";
  button.setAttribute("aria-label", `Copy ${term}`);
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(description);
      button.textContent = "Copied";
      window.setTimeout(() => {
        button.textContent = "Copy";
      }, 1500);
    } catch {
      button.textContent = "Copy failed";
    }
  });
  value.append(button);
  wrapper.append(textElement("dt", term), value);
  return wrapper;
}

function renderEvidence(evidence, headingTag = "h4") {
  const section = document.createElement("section");
  section.className = "diagnosis-section";
  section.append(textElement(headingTag, "Evidence"));
  if (!evidence.length) {
    section.append(textElement("p", "No log Evidence was retained for this Diagnosis.", "muted-copy"));
    return section;
  }
  const list = document.createElement("ol");
  list.className = "evidence-list";
  for (const item of evidence) {
    const entry = document.createElement("li");
    entry.value = item.line_id;
    entry.append(
      textElement("span", String(item.line_id), "evidence-line-id"),
      textElement("code", item.text),
    );
    list.append(entry);
  }
  section.append(list);
  return section;
}

function renderActions(actions, headingTag = "h4") {
  if (!actions.length) {
    return null;
  }
  const section = document.createElement("section");
  section.className = "diagnosis-section";
  section.append(textElement(headingTag, "Recommended actions"));
  const list = document.createElement("ul");
  list.className = "recommendation-list";
  for (const action of actions) {
    list.append(textElement("li", action));
  }
  section.append(list);
  return section;
}

function renderDiagnosis(diagnosis, includeDetailLink = true) {
  const card = document.createElement("article");
  card.className = "diagnosis-card";
  if (diagnosis.effective) {
    card.classList.add("is-effective");
  }
  if (diagnosis.validation_status === "REJECTED") {
    card.classList.add("is-rejected");
  }

  const heading = document.createElement("div");
  heading.className = "diagnosis-heading";
  const labels = document.createElement("div");
  labels.className = "diagnosis-labels";
  labels.append(sourceBadge(diagnosis.source));
  labels.append(validationBadge(diagnosis.validation_status));
  if (diagnosis.effective) {
    labels.append(textElement("span", "Effective", "effective-badge"));
  }
  const rootCause = textElement(
    "h3",
    diagnosis.root_cause || "No Root Cause was produced",
    "diagnosis-root-cause",
  );
  heading.append(labels, rootCause);
  card.append(heading);

  if (diagnosis.validation_status === "REJECTED") {
    card.append(textElement(
      "p",
      "Rejected AI attempt — retained for provenance and never used as the effective Diagnosis.",
      "rejection-note",
    ));
  }
  if (diagnosis.source === "REUSED") {
    card.append(textElement(
      "p",
      `Content resolved from original Diagnosis ${diagnosis.content_diagnosis_id}.`,
      "reuse-note",
    ));
  }

  const facts = document.createElement("dl");
  facts.className = "diagnosis-facts";
  const confidence = diagnosis.confidence === null
    ? "Unavailable"
    : `${Math.round(diagnosis.confidence * 100)}%`;
  facts.append(
    definitionItem("Classification", diagnosis.classification || "UNKNOWN"),
    definitionItem("Confidence", confidence),
    definitionItem("Retry", diagnosis.retry_decision || "UNKNOWN"),
    definitionItem(
      "Review",
      diagnosis.operator_review_required === null
        ? "Unavailable"
        : diagnosis.operator_review_required ? "Operator required" : "Not required",
    ),
  );
  card.append(facts);
  card.append(textElement("p", "Confidence is the diagnosis score, not a measured accuracy rate.", "heading-copy"));
  card.append(textElement("p", "Validation describes automated checks, not operator confirmation of the cause. AI checks include matching cited evidence to the log.", "heading-copy"));
  card.append(renderEvidence(diagnosis.evidence));

  const actions = renderActions(diagnosis.recommended_actions);
  if (actions) {
    card.append(actions);
  }
  if (diagnosis.validation_errors.length) {
    const validation = document.createElement("section");
    validation.className = "diagnosis-section validation-errors";
    validation.append(textElement("h4", "Validation errors"));
    const list = document.createElement("ul");
    for (const error of diagnosis.validation_errors) {
      list.append(textElement("li", error));
    }
    validation.append(list);
    card.append(validation);
  }

  const provenance = document.createElement("footer");
  provenance.className = "diagnosis-provenance";
  const versionDetails = document.createElement("details");
  versionDetails.className = "diagnosis-version-details";
  versionDetails.append(
    textElement("summary", "Version details"),
    textElement(
      "code",
      `schema v${diagnosis.diagnosis_schema_version} · prompt ${diagnosis.prompt_version || "—"} · rule ${diagnosis.rule_version || "—"}`,
    ),
  );
  provenance.append(
    textElement("code", diagnosis.id),
    versionDetails,
  );
  if (includeDetailLink) {
    const inspect = document.createElement("a");
    inspect.className = "diagnosis-detail-link";
    inspect.href = diagnosisHref(diagnosis.id);
    inspect.textContent = "Inspect Diagnosis →";
    provenance.append(inspect);
  }
  card.append(provenance);
  return card;
}

function renderSignature(signature) {
  const panel = document.createElement("details");
  panel.className = "signature-panel";
  const summary = document.createElement("summary");
  summary.textContent = "Error Signature";
  const content = document.createElement("dl");
  content.className = "signature-fields";
  content.append(
    definitionItem("Fingerprint", signature.fingerprint),
    definitionItem("Version", String(signature.fingerprint_version)),
    definitionItem("Exception", signature.exception_class || "Unavailable"),
    definitionItem("Vendor code", signature.vendor_error_code || "Unavailable"),
    definitionItem("Operator", signature.operator_type || "Unavailable"),
    definitionItem("Message", signature.normalized_message || "Unavailable"),
  );
  const explore = document.createElement("a");
  explore.className = "signature-explore-link";
  explore.href = `/ui/?view=signatures&signature=${encodeURIComponent(signature.id)}`;
  explore.textContent = "Explore recurring occurrences →";
  panel.append(summary, content, explore);
  return panel;
}

function renderFailure(failure, index) {
  const card = document.createElement("article");
  card.className = "failure-card";
  const heading = document.createElement("header");
  heading.className = "failure-heading";
  const title = document.createElement("div");
  title.append(
    textElement("p", `Occurrence ${index + 1}`, "table-kicker"),
    textElement("h3", `Try ${failure.try_number} · ${failure.state}`),
  );
  if (failure.is_initial_failure) {
    title.append(textElement("span", currentLanguage === "ko" ? "최초 실패" : "Initial failure", "status-pill"));
  }
  if (failure.is_final_failure) {
    title.append(textElement("span", currentLanguage === "ko" ? "최종 실패 알림 대상" : "Final failure notification", "status-pill"));
  }
  const metadata = document.createElement("div");
  metadata.className = "failure-metadata";
  metadata.append(
    textElement("span", formatTimestamp(failure.observed_at).primary),
    textElement("span", `run ${failure.dag_run_id}`),
    textElement("span", `map ${failure.map_index}`),
  );
  heading.append(title, metadata);
  card.append(heading);

  const airflowLogUrl = safeHttpUrl(failure.airflow_log_url);
  if (airflowLogUrl) {
    const logLink = document.createElement("a");
    logLink.className = "airflow-link";
    logLink.href = airflowLogUrl;
    logLink.target = "_blank";
    logLink.rel = "noreferrer noopener";
    logLink.textContent = "Open Task log in Airflow ↗";
    card.append(logLink);
  }

  const diagnoses = document.createElement("section");
  diagnoses.className = "diagnosis-stack";
  diagnoses.append(textElement("h3", "Diagnosis attempts", "stack-title"));
  if (!failure.diagnoses.length) {
    diagnoses.append(textElement("p", "Diagnosis has not been persisted yet.", "muted-copy"));
  } else {
    for (const diagnosis of failure.diagnoses) {
      diagnoses.append(renderDiagnosis(diagnosis));
    }
  }
  card.append(diagnoses);
  if (failure.error_signature) {
    card.append(renderSignature(failure.error_signature));
  } else {
    card.append(textElement(
      "p",
      "Unsignable Failure — no stable Error Signature was available for correlation.",
      "unsignable-note",
    ));
  }
  return card;
}

function transitionStatusLabel(transition) {
  if (
    ["RESOLVED", "IGNORED"].includes(transition.previous_status)
    && transition.status === "OPEN"
  ) {
    return "REOPENED";
  }
  return transition.status;
}

function renderTransitions(transitions) {
  elements.transitionHistory.replaceChildren();
  elements.transitionCount.textContent = String(transitions.length);
  elements.transitionHistory.classList.toggle("is-scrollable", transitions.length > 3);
  elements.transitionEmpty.hidden = transitions.length !== 0;
  const orderedTransitions = [...transitions].reverse();
  for (const transition of orderedTransitions) {
    const item = document.createElement("li");
    const heading = document.createElement("div");
    heading.className = "transition-heading";
    const statusLabel = transitionStatusLabel(transition);
    heading.append(
      textElement("strong", translatedText(statusLabel)),
      textElement("time", formatTimestamp(transition.created_at).primary),
    );
    const detail = textElement(
      "p",
      `${translatedText(transition.previous_status)} → ${translatedText(statusLabel)} · ${transition.actor}`,
    );
    item.append(heading, detail);
    if (transition.reason) {
      item.append(textElement("blockquote", transition.reason));
    }
    elements.transitionHistory.append(item);
  }
}

function closeTransitionDialog() {
  if (elements.transitionDialog.open) {
    elements.transitionDialog.close();
  }
  pendingOperatorAction = null;
  elements.transitionError.hidden = true;
}

function openTransitionDialog(action) {
  pendingOperatorAction = action;
  elements.transitionDialogTitle.textContent = action.label;
  elements.transitionDialogCopy.textContent = action.copy;
  elements.transitionReason.value = "";
  elements.transitionError.hidden = true;
  elements.transitionConfirm.textContent = action.label;
  elements.transitionDialog.showModal();
  elements.transitionReason.focus();
}

function renderOperatorControls(status, transitions) {
  const isOperator = ["operator", "admin"].includes(storedSession().role);
  elements.operatorControls.hidden = !isOperator;
  elements.viewerStateNote.hidden = isOperator;
  elements.operatorActions.replaceChildren();
  if (!isOperator) {
    return;
  }

  const terminalActions = TERMINAL_OPERATOR_ACTIONS[status];
  const lastTransition = transitions[transitions.length - 1];
  const canOverrideTerminal = storedSession().role === "admin"
    || (lastTransition && currentUser && lastTransition.actor === currentUser.email);
  const actions = terminalActions
    ? canOverrideTerminal ? terminalActions : []
    : OPERATOR_ACTIONS[status] || [];
  elements.operatorHelp.textContent = terminalActions
    ? canOverrideTerminal
      ? "This terminal Incident can be changed by its last operator or an Admin."
      : "Only the operator who made the terminal change or an Admin can change this Incident."
    : "Choose an explicit state change. Every change is added to the audit trail.";
  for (const action of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = action.status === "IGNORED"
      ? "button button-danger"
      : action.status === "RESOLVED"
        ? "button button-primary"
        : "button button-secondary";
    button.textContent = action.label;
    button.addEventListener("click", () => openTransitionDialog(action));
    elements.operatorActions.append(button);
  }
}

function renderHumanDiagnosis(diagnosis) {
  const isOperator = ["operator", "admin"].includes(storedSession().role);
  elements.humanDiagnosisContent.replaceChildren();
  elements.humanDiagnosisActions.replaceChildren();
  elements.humanDiagnosisActions.hidden = !isOperator;
  elements.humanDiagnosisRevision.hidden = !diagnosis;
  if (!diagnosis) {
    elements.humanDiagnosisContent.append(
      textElement("p", translatedText("No operator-confirmed diagnosis has been published."), "aside-empty"),
    );
  } else {
    elements.humanDiagnosisRevision.textContent = `리비전 ${diagnosis.revision}`;
    const rootCause = document.createElement("div");
    rootCause.className = "human-diagnosis-root-cause";
    rootCause.append(definitionItem("근본 원인", diagnosis.root_cause));
    const assessment = document.createElement("dl");
    assessment.className = "human-diagnosis-assessment";
    assessment.append(
      definitionItem("분류", humanClassificationLabel(diagnosis.classification)),
      definitionItem("재시도 판단", humanRetryLabel(diagnosis.retry_decision)),
    );
    elements.humanDiagnosisContent.append(rootCause, assessment);
    if ((diagnosis.recommended_actions || []).length) {
      const actionsSection = document.createElement("section");
      actionsSection.className = "human-diagnosis-actions-section";
      actionsSection.append(textElement("h3", "권장 조치"));
      const actions = document.createElement("ul");
      for (const action of diagnosis.recommended_actions || []) {
        actions.append(textElement("li", action));
      }
      actionsSection.append(actions);
      elements.humanDiagnosisContent.append(actionsSection);
    }
    if (diagnosis.operator_notes) {
      elements.humanDiagnosisContent.append(definitionItem("운영자 메모", diagnosis.operator_notes));
    }
    const meta = document.createElement("dl");
    meta.className = "human-diagnosis-meta";
    meta.append(
      definitionItem("작성자", diagnosis.actor_identity),
      definitionItem("확정 시각", formatTimestamp(diagnosis.created_at).primary),
    );
    elements.humanDiagnosisContent.append(meta);
  }
  if (!isOperator) {
    return;
  }
  const publish = document.createElement("button");
  publish.type = "button";
  publish.className = "button button-secondary";
  publish.textContent = translatedText(diagnosis ? "Edit" : "Add diagnosis");
  publish.addEventListener("click", openHumanDiagnosisDialog);
  elements.humanDiagnosisActions.append(publish);
  if (diagnosis) {
    const withdraw = document.createElement("button");
    withdraw.type = "button";
    withdraw.className = "button button-danger";
    withdraw.textContent = translatedText("Withdraw");
    withdraw.addEventListener("click", submitHumanDiagnosisWithdrawal);
    elements.humanDiagnosisActions.append(withdraw);
  }
}

function humanClassificationLabel(value) {
  return {
    DAG_CODE: "DAG 코드",
    AIRFLOW_PLATFORM: "Airflow 플랫폼",
    SOURCE_DATABASE: "원본 데이터베이스",
    NETWORK: "네트워크",
    AUTHENTICATION: "인증",
    AUTHORIZATION: "권한",
    RESOURCE: "리소스",
    DATA_QUALITY: "데이터 품질",
    EXTERNAL_SYSTEM: "외부 시스템",
    CONFIGURATION: "설정",
    UNKNOWN: "알 수 없음",
  }[value] || value;
}

function humanRetryLabel(value) {
  return { RETRYABLE: "재시도 가능", NOT_RETRYABLE: "재시도 불가", UNKNOWN: "판단 보류" }[value] || value;
}

function humanRevisionLabel(revision) {
  if (revision.action === "WITHDRAW") return "철회";
  return revision.revision === 1 ? "최초 등록" : "수정";
}

function closeHumanDiagnosisDialog() {
  if (elements.humanDiagnosisDialog.open) {
    elements.humanDiagnosisDialog.close();
  }
  elements.humanDiagnosisError.hidden = true;
}

async function renderHumanDiagnosisHistory() {
  elements.humanDiagnosisHistory.replaceChildren();
  if (!currentIncidentId) {
    return;
  }
  const response = await fetch(
    `/api/v1/incidents/${encodeURIComponent(currentIncidentId)}/human-diagnoses`,
    { headers: authHeaders() },
  );
  if (!response.ok) {
    return;
  }
  const history = await response.json();
  for (const [index, revision] of history.items.entries()) {
    const item = document.createElement("li");
    const details = document.createElement("details");
    details.open = index === 0;
    const summary = document.createElement("summary");
    summary.append(
      textElement("strong", `리비전 ${revision.revision} · ${humanRevisionLabel(revision)}`),
      textElement("time", formatTimestamp(revision.created_at).primary),
    );
    details.append(summary);
    const fields = document.createElement("dl");
    fields.className = "human-diagnosis-revision-fields";
    if (revision.root_cause) {
      const rootCause = definitionItem("근본 원인", revision.root_cause);
      rootCause.className = "is-wide";
      fields.append(rootCause);
    }
    if (revision.classification) fields.append(definitionItem("분류", humanClassificationLabel(revision.classification)));
    if (revision.retry_decision) fields.append(definitionItem("재시도 판단", humanRetryLabel(revision.retry_decision)));
    if (revision.operator_notes) {
      const notes = definitionItem("운영자 메모", revision.operator_notes);
      notes.className = "is-wide";
      fields.append(notes);
    }
    if (revision.change_reason) {
      const reason = definitionItem("변경 사유", revision.change_reason);
      reason.className = "is-wide";
      fields.append(reason);
    }
    let actionsSection;
    if ((revision.recommended_actions || []).length) {
      actionsSection = document.createElement("section");
      actionsSection.className = "human-diagnosis-actions-section";
      actionsSection.append(textElement("h3", "권장 조치"));
      const list = document.createElement("ul");
      for (const action of revision.recommended_actions || []) list.append(textElement("li", action));
      actionsSection.append(list);
    }
    const author = definitionItem("작성자", revision.actor_identity);
    author.className = "is-wide";
    fields.append(author);
    details.append(fields);
    if (actionsSection) details.append(actionsSection);
    item.append(details);
    elements.humanDiagnosisHistory.append(item);
  }
}

function openHumanDiagnosisDialog() {
  elements.humanDiagnosisForm.reset();
  elements.humanDiagnosisRetry.value = "UNKNOWN";
  elements.humanDiagnosisError.hidden = true;
  elements.humanDiagnosisDialog.showModal();
  elements.humanDiagnosisRootCause.focus();
}

async function humanDiagnosisRevision() {
  if (!currentIncidentId) {
    return 0;
  }
  const response = await fetch(
    `/api/v1/incidents/${encodeURIComponent(currentIncidentId)}/human-diagnoses`,
    { headers: authHeaders() },
  );
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const history = await response.json();
  return history.items.length ? history.items[0].revision : 0;
}

async function submitHumanDiagnosis() {
  if (!currentIncidentId) {
    return;
  }
  const actions = elements.humanDiagnosisActionsInput.value
    .split("\n").map((value) => value.trim()).filter(Boolean);
  const body = {
    expected_revision: await humanDiagnosisRevision(),
    classification: elements.humanDiagnosisClassification.value,
    root_cause: elements.humanDiagnosisRootCause.value.trim(),
    recommended_actions: actions,
    retry_decision: elements.humanDiagnosisRetry.value,
    operator_notes: elements.humanDiagnosisNotes.value.trim() || null,
    change_reason: elements.humanDiagnosisChangeReason.value.trim() || null,
  };
  try {
    const response = await fetch(
      `/api/v1/incidents/${encodeURIComponent(currentIncidentId)}/human-diagnoses`,
      { method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" }, body: JSON.stringify(body) },
    );
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    closeHumanDiagnosisDialog();
    if (await loadIncidentDetail(currentIncidentId)) {
      showDetailFeedback(translatedText("Operator-confirmed diagnosis published."));
    }
  } catch (error) {
    elements.humanDiagnosisError.textContent = error instanceof Error ? error.message : translatedText("Unable to publish diagnosis");
    elements.humanDiagnosisError.hidden = false;
  }
}

async function submitHumanDiagnosisWithdrawal() {
  if (!currentIncidentId) {
    return;
  }
  const reason = window.prompt(translatedText("Reason for withdrawal:"));
  if (!reason) {
    return;
  }
  try {
    const response = await fetch(
      `/api/v1/incidents/${encodeURIComponent(currentIncidentId)}/human-diagnoses/withdraw`,
      { method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: await humanDiagnosisRevision(), reason }) },
    );
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    if (await loadIncidentDetail(currentIncidentId)) {
      showDetailFeedback(translatedText("Operator-confirmed diagnosis withdrawn."));
    }
  } catch (error) {
    showDetailFeedback(error instanceof Error ? error.message : translatedText("Unable to withdraw diagnosis"));
  }
}

function showDetailFeedback(message) {
  elements.detailFeedback.textContent = message;
  elements.detailFeedback.hidden = false;
  elements.detailFeedback.focus();
}

function latestFailureDiagnosis(failures) {
  const latestFailure = failures.at(-1);
  return latestFailure?.diagnoses.find((diagnosis) => (
    diagnosis.effective && diagnosis.validation_status === "PASSED"
  )) || null;
}

function renderCurrentDiagnosis(payload) {
  const content = elements.currentDiagnosisContent;
  content.replaceChildren();
  elements.currentDiagnosisPanel.open = !payload.current_human_diagnosis;
  const diagnosis = latestFailureDiagnosis(payload.failures);
  const latestFailure = payload.failures.at(-1);
  if (latestFailure) {
    content.append(textElement("p", formatTimestamp(latestFailure.observed_at).primary, "heading-copy"));
  }
  if (!diagnosis) {
    content.append(textElement("p", "No validated diagnosis is available for the latest failure. Review its task log or earlier attempts in the history.", "heading-copy"));
  } else {
    content.append(textElement("h3", diagnosis.root_cause || "No Root Cause was produced", "diagnosis-root-cause"));
    const facts = document.createElement("dl");
    facts.className = "diagnosis-facts";
    facts.append(
      definitionItem("Source", diagnosis.source),
      definitionItem("Retry", diagnosis.retry_decision || "UNKNOWN"),
      definitionItem("Review", diagnosis.operator_review_required === null
        ? "Unavailable" : diagnosis.operator_review_required ? "Operator required" : "Not required"),
    );
    content.append(facts);
    const actions = renderActions(diagnosis.recommended_actions);
    if (actions) content.append(actions);
    const evidence = document.createElement("details");
    evidence.className = "diagnosis-technical-details";
    evidence.append(textElement("summary", "Evidence and diagnosis details"), renderDiagnosis(diagnosis));
    content.append(evidence);
  }
  const logUrl = safeHttpUrl(latestFailure?.airflow_log_url);
  if (latestFailure?.airflow_log_url && logUrl) {
    const link = contextLink("Open Task log in Airflow ↗", logUrl);
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    content.append(link);
  }
}

function renderIncidentDetail(payload) {
  const incident = payload.incident;
  currentIncidentId = incident.id;
  currentIncidentStatus = incident.status;
  elements.detailStatus.textContent = incident.status.replaceAll("_", " ");
  elements.detailStatus.dataset.status = incident.status;
  elements.detailStateHelp.textContent = {
    OPEN: "This incident is awaiting investigation.",
    ACKNOWLEDGED: "An operator has started investigating this incident.",
    RECOVERED: "The task has recovered. Confirm whether follow-up is complete before resolving the incident.",
    RESOLVED: "An operator has closed this incident after confirming remediation.",
    IGNORED: "An operator has closed this incident without further action.",
  }[incident.status];
  elements.detailEnvironment.textContent = incident.environment;
  elements.detailTitle.textContent = incident.dag_id;
  elements.detailTask.textContent = incident.task_id;
  elements.detailFailureCount.textContent = String(incident.failure_count);
  elements.detailFirstSeen.textContent = formatTimestamp(incident.first_failure_at).primary;
  elements.detailLastSeen.textContent = formatTimestamp(incident.last_failure_at).primary;
  elements.detailIncidentId.textContent = incident.id;
  elements.detailSignatureLink.hidden = !incident.error_signature_id;
  if (incident.error_signature_id) {
    elements.detailSignatureLink.href = `/ui/?view=signatures&signature=${encodeURIComponent(incident.error_signature_id)}`;
  } else {
    elements.detailSignatureLink.removeAttribute("href");
  }
  elements.detailFailures.replaceChildren();
  for (const [index, failure] of payload.failures.entries()) {
    elements.detailFailures.append(renderFailure(failure, index));
  }
  renderTransitions(payload.transitions);
  renderOperatorControls(incident.status, payload.transitions);
  renderHumanDiagnosis(payload.current_human_diagnosis);
  renderCurrentDiagnosis(payload);
  void renderHumanDiagnosisHistory();
  elements.detailContent.hidden = false;
}

function detailBackHref() {
  const params = new URLSearchParams(window.location.search);
  params.delete("incident");
  const query = params.toString();
  return query ? `/ui/?${query}` : "/ui/";
}

function normalizedErrorDetail(detail, fallback) {
  if (typeof detail === "string" && detail) {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (typeof item === "string") {
        return item;
      }
      if (!item || typeof item.msg !== "string") {
        return [];
      }
      const location = Array.isArray(item.loc)
        ? item.loc.filter((part) => part !== "body").join(".")
        : "";
      return location ? `${location}: ${item.msg}` : item.msg;
    });
    return messages.length ? messages.join("; ") : fallback;
  }
  if (detail && typeof detail.message === "string") {
    return detail.message;
  }
  return fallback;
}

async function errorDetail(response) {
  const fallback = `Request failed with status ${response.status}`;
  try {
    const payload = await response.json();
    return normalizedErrorDetail(payload.detail, fallback);
  } catch {
    return fallback;
  }
}

async function loadSignatures() {
  setSignatureLoading(true);
  elements.signatureError.hidden = true;
  const params = signatureQueryFromFilters();
  updateSignatureUrl(params);
  try {
    const response = await fetch(`/api/v1/error-signatures?${params.toString()}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    renderSignaturePage(await response.json());
    return true;
  } catch (error) {
    elements.signatureResults.hidden = true;
    elements.signatureEmpty.hidden = true;
    elements.signatureError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Error Signatures";
    elements.signatureError.hidden = false;
    return false;
  } finally {
    setSignatureLoading(false);
  }
}

async function loadSignatureDetail(signatureId) {
  if (currentSignatureId !== signatureId) {
    currentSignatureTrendDays = 7;
  }
  currentSignatureId = signatureId;
  elements.signatureDetailBack.href = signatureDetailBackHref();
  elements.signatureDetailLoading.hidden = false;
  elements.signatureDetailError.hidden = true;
  elements.signatureDetailContent.hidden = true;
  try {
    const response = await fetch(`/api/v1/error-signatures/${encodeURIComponent(signatureId)}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    const signature = await response.json();
    currentSignature = signature;
    updateSignatureTrendControls();
    const occurrenceParams = occurrenceQueryFromUrl();
    const trendParams = trendQueryForSignature(signature);
    const [occurrenceResponse, trendResponse] = await Promise.all([
      fetch(
        `/api/v1/error-signatures/${encodeURIComponent(signatureId)}/occurrences?${occurrenceParams.toString()}`,
        { headers: authHeaders() },
      ),
      fetch(
        `/api/v1/error-signatures/${encodeURIComponent(signatureId)}/trend?${trendParams.toString()}`,
        { headers: authHeaders() },
      ),
    ]);
    for (const relatedResponse of [occurrenceResponse, trendResponse]) {
      if (!relatedResponse.ok) {
        const detail = await errorDetail(relatedResponse);
        if (relatedResponse.status === 401 || relatedResponse.status === 503) {
          clearSession();
          showAuth(detail);
          return false;
        }
        throw new Error(detail);
      }
    }

    renderSignatureIdentity(signature);
    renderSignatureOccurrences(await occurrenceResponse.json());
    renderSignatureTrend(await trendResponse.json());
    elements.signatureDetailContent.hidden = false;
    return true;
  } catch (error) {
    elements.signatureDetailError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Error Signature detail";
    elements.signatureDetailError.hidden = false;
    return false;
  } finally {
    elements.signatureDetailLoading.hidden = true;
  }
}

async function refreshSignatureTrend(days) {
  if (!currentSignatureId || !currentSignature) {
    return;
  }
  currentSignatureTrendDays = days;
  updateSignatureTrendControls();
  elements.signatureTrend7.disabled = true;
  elements.signatureTrend30.disabled = true;
  try {
    const trendParams = trendQueryForSignature(currentSignature, days);
    const response = await fetch(
      `/api/v1/error-signatures/${encodeURIComponent(currentSignatureId)}/trend?${trendParams.toString()}`,
      { headers: authHeaders() },
    );
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return;
      }
      throw new Error(detail);
    }
    renderSignatureTrend(await response.json());
  } catch (error) {
    elements.signatureDetailError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Error Signature trend";
    elements.signatureDetailError.hidden = false;
  } finally {
    elements.signatureTrend7.disabled = false;
    elements.signatureTrend30.disabled = false;
  }
}

async function loadDiagnoses() {
  setDiagnosisLoading(true);
  elements.diagnosisError.hidden = true;
  const params = diagnosisQueryFromFilters();
  updateDiagnosisUrl(params);
  try {
    const response = await fetch(`/api/v1/diagnoses/history?${params.toString()}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    renderDiagnosisPage(await response.json());
    void loadDiagnosisSummary();
    return true;
  } catch (error) {
    elements.diagnosisResults.hidden = true;
    elements.diagnosisEmpty.hidden = true;
    elements.diagnosisError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Diagnosis History";
    elements.diagnosisError.hidden = false;
    return false;
  } finally {
    setDiagnosisLoading(false);
  }
}

function reportStatusBadge(status) {
  const badge = textElement("span", status, "status-badge");
  badge.dataset.status = status;
  return badge;
}

function setReportSchedulerStatus(status) {
  elements.reportSchedulerStatus.textContent = status === "ONLINE" ? "정상" : "오프라인";
  elements.reportSchedulerStatus.dataset.status = status;
}

function formatScheduleTimestamp(value, timezone) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return "Unavailable";
  }
  return `${formatDateTime(timestamp, timezone)} ${timezoneLabel(timestamp, timezone)} · ${formatDateTime(timestamp, "UTC")} UTC`;
}

function scheduleSummary(schedule) {
  const card = document.createElement("article");
  card.className = "report-schedule-card";
  const heading = textElement("h3", schedule.display_name);
  const copy = textElement(
    "p",
    `${schedule.enabled
      ? "자동 리포트가 활성화되어 있습니다."
      : "자동 리포트가 비활성화되어 있습니다."} · ${schedule.environment}`,
  );
  const next = textElement(
    "p",
    schedule.next_run_at
      ? `다음 실행: ${formatScheduleTimestamp(schedule.next_run_at, schedule.timezone)}`
      : "다음 실행: 자동 리포트 비활성",
    "muted-copy",
  );
  const applied = textElement(
    "p",
    schedule.applied_revision === schedule.revision ? "스케줄러 적용 완료" : "스케줄러 적용 대기 중",
    "muted-copy",
  );
  const heartbeat = textElement(
    "p",
    schedule.last_heartbeat_at
      ? `마지막 heartbeat: ${formatTimestamp(schedule.last_heartbeat_at).primary}`
      : "마지막 heartbeat: 없음",
    "muted-copy",
  );
  card.append(heading, copy, next, applied, heartbeat);
  return card;
}

function populateReportScheduleForm(schedule) {
  currentReportSchedule = schedule;
  elements.reportScheduleId.value = schedule?.id || "";
  elements.reportScheduleRevision.value = schedule?.revision ? String(schedule.revision) : "";
  elements.reportScheduleName.value = schedule?.display_name || "Production Daily Report";
  elements.reportScheduleTitle.value = schedule?.report_title || "DagSentry 일일 장애 리포트";
  elements.reportScheduleEnvironment.value = schedule?.environment || "production";
  elements.reportScheduleNotificationConnection.value = schedule?.notification_connection_id || "";
  elements.reportScheduleTime.value = schedule?.run_at_local_time?.slice(0, 5) || "09:00";
  elements.reportScheduleTimezone.value = schedule?.timezone || "Asia/Seoul";
  elements.reportScheduleAiSummary.checked = schedule?.use_ai_summary ?? false;
  elements.reportScheduleEnabled.checked = schedule?.enabled ?? true;
  elements.reportManualRunForm.hidden = !schedule;
  const localToday = new Intl.DateTimeFormat("en-CA", { timeZone: schedule?.timezone || "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const yesterday = new Date(Date.parse(`${localToday}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10);
  elements.reportManualDate.max = yesterday;
  if (!elements.reportManualDate.value) {
    elements.reportManualDate.value = yesterday;
  }
  renderReportSchedulePreview();
}

function renderReportSchedulePreview() {
  const time = elements.reportScheduleTime.value || "09:00";
  const timezone = elements.reportScheduleTimezone.value || "Asia/Seoul";
  elements.reportSchedulePreview.replaceChildren(
    textElement("p", elements.reportScheduleEnabled.checked
      ? `매일 ${time} ${timezone}에 실행됩니다.`
      : "자동 리포트가 비활성화되어 있습니다."),
    textElement("p", "전일 UTC 기준 데이터를 집계합니다."),
  );
}

function renderNotificationConnections() {
  const selected = elements.reportScheduleNotificationConnection.value;
  elements.reportScheduleNotificationConnection.replaceChildren(
    new Option("기본 알림 설정", ""),
    ...reportNotificationConnections.map((connection) => new Option(
      `${connection.display_name} · ${connection.environment}`,
      connection.id,
    )),
  );
  elements.reportScheduleNotificationConnection.value = selected;
}

function renderReportSchedules(schedules, runs, schedulerStatus) {
  setReportSchedulerStatus(schedulerStatus.status);
  elements.reportScheduleList.replaceChildren(...schedules.map(scheduleSummary));
  if (!schedules.length) {
    elements.reportScheduleList.append(textElement("p", "아직 저장된 리포트 스케줄이 없습니다.", "aside-empty"));
  }
  const isAdmin = currentUser?.role === "ADMIN";
  elements.reportScheduleForm.hidden = !isAdmin;
  if (isAdmin) {
    const selected = schedules.find((schedule) => schedule.id === currentReportSchedule?.id)
      || schedules[0]
      || null;
    populateReportScheduleForm(selected);
  }
  elements.reportScheduleRuns.replaceChildren();
  if (!runs.length) {
    elements.reportScheduleRuns.append(textElement("p", "아직 실행 이력이 없습니다.", "aside-empty"));
    return;
  }
  const latest = runs[0];
  const statusLabels = {
    CLAIMED: "대기 중",
    RUNNING: "실행 중",
    SUCCEEDED: "성공",
    FAILED: "실패",
    SKIPPED: "건너뜀",
  };
  const deliveryLabels = {
    CLAIMED: "전송 대기",
    RUNNING: "전송 중",
    SUCCEEDED: "전송 완료",
    FAILED: "전송 실패",
    SKIPPED: "이미 전송됨",
  };
  const details = document.createElement("dl");
  details.className = "report-execution-grid";
  const appendDetail = (label, value) => {
    details.append(textElement("dt", label), textElement("dd", value));
  };
  appendDetail(
    "실행 시각",
    formatTimestamp(latest.started_at || latest.scheduled_for).primary,
  );
  appendDetail("상태", statusLabels[latest.status] || latest.status);
  appendDetail("리포트 날짜", latest.report_date);
  appendDetail("전송 상태", deliveryLabels[latest.status] || latest.status);
  if (latest.report_id) {
    details.append(textElement("dt", "리포트"), contextLink("상세 보기", reportHref(latest.report_id)));
  }
  elements.reportScheduleRuns.append(details);
}

async function loadReportSchedules() {
  elements.reportScheduleError.hidden = true;
  try {
    const requests = [
      fetch("/api/v1/daily-report-schedules", { headers: authHeaders() }),
      fetch("/api/v1/daily-report-schedules/runs?limit=5", { headers: authHeaders() }),
      fetch("/api/v1/daily-report-schedules/status", { headers: authHeaders() }),
    ];
    if (currentUser?.role === "ADMIN") {
      requests.push(fetch("/api/v1/admin/connections?limit=100", { headers: authHeaders() }));
    }
    const responses = await Promise.all(requests);
    if (responses.some((response) => !response.ok)) {
      const response = responses.find((item) => !item.ok);
      throw new Error(await errorDetail(response));
    }
    const schedules = await responses[0].json();
    const runs = await responses[1].json();
    const schedulerStatus = await responses[2].json();
    if (responses[3]) {
      const connections = await responses[3].json();
      reportNotificationConnections = connections.items.filter((connection) => (
        connection.purpose === "NOTIFICATION" && connection.enabled
      ));
      renderNotificationConnections();
    }
    renderReportSchedules(schedules.items, runs.items, schedulerStatus);
  } catch (error) {
    elements.reportScheduleError.textContent = error instanceof Error
      ? error.message
      : "Daily Report 스케줄을 불러오지 못했습니다.";
    elements.reportScheduleError.hidden = false;
  }
}

async function saveReportSchedule() {
  const body = {
    display_name: elements.reportScheduleName.value.trim(),
    report_title: elements.reportScheduleTitle.value.trim(),
    environment: elements.reportScheduleEnvironment.value.trim(),
    notification_connection_id: elements.reportScheduleNotificationConnection.value || null,
    use_ai_summary: elements.reportScheduleAiSummary.checked,
    enabled: elements.reportScheduleEnabled.checked,
    run_at_local_time: elements.reportScheduleTime.value,
    timezone: elements.reportScheduleTimezone.value,
  };
  const scheduleId = elements.reportScheduleId.value;
  if (scheduleId) {
    body.expected_revision = Number(elements.reportScheduleRevision.value);
  }
  elements.reportScheduleSave.disabled = true;
  elements.reportScheduleError.hidden = true;
  try {
    const response = await fetch(
      scheduleId
        ? `/api/v1/admin/daily-report-schedules/${encodeURIComponent(scheduleId)}`
        : "/api/v1/admin/daily-report-schedules",
      {
        method: scheduleId ? "PUT" : "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    currentReportSchedule = await response.json();
    elements.reportScheduleFeedback.textContent = "설정을 저장했습니다. 스케줄러가 다음 동기화 때 적용합니다.";
    elements.reportScheduleFeedback.hidden = false;
    await loadReportSchedules();
  } catch (error) {
    elements.reportScheduleError.textContent = error instanceof Error
      ? error.message
      : "Daily Report 스케줄을 저장하지 못했습니다.";
    elements.reportScheduleError.hidden = false;
  } finally {
    elements.reportScheduleSave.disabled = false;
  }
}

async function requestManualReportRun() {
  if (!currentReportSchedule) {
    return;
  }
  elements.reportManualRun.disabled = true;
  elements.reportScheduleError.hidden = true;
  try {
    const response = await fetch(
      `/api/v1/admin/daily-report-schedules/${encodeURIComponent(currentReportSchedule.id)}/runs`,
      {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ report_date: elements.reportManualDate.value }),
      },
    );
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    elements.reportScheduleFeedback.textContent = "Daily Report 실행을 스케줄러에 요청했습니다.";
    elements.reportScheduleFeedback.hidden = false;
    await loadReportSchedules();
  } catch (error) {
    elements.reportScheduleError.textContent = error instanceof Error
      ? error.message
      : "Daily Report 실행을 요청하지 못했습니다.";
    elements.reportScheduleError.hidden = false;
  } finally {
    elements.reportManualRun.disabled = false;
  }
}

function reportHref(reportId) {
  const params = new URLSearchParams(window.location.search);
  params.set("view", "reports");
  params.set("report", reportId);
  return `/ui/?${params.toString()}`;
}

function renderReportRows(items) {
  elements.reportRows.replaceChildren();
  for (const report of items) {
    const row = document.createElement("tr");
    appendCell(row, report.report_date);
    appendCell(row, textElement("span", report.environment, "environment-chip"));
    appendCell(row, String(report.statistics.failure_attempts), "numeric");
    appendCell(row, String(report.statistics.affected_dag_runs), "numeric");
    appendCell(row, String(report.statistics.incidents.unresolved), "numeric");
    appendCell(row, reportStatusBadge(report.status));
    appendCell(row, timestampBlock(report.created_at));
    appendCell(row, contextLink("Inspect", reportHref(report.id)));
    elements.reportRows.append(row);
  }
}

function renderReportPage(payload) {
  currentReportTotal = payload.total;
  elements.reportError.hidden = true;
  elements.reportEmpty.hidden = payload.items.length !== 0;
  elements.reportResults.hidden = payload.items.length === 0;
  renderReportRows(payload.items);
  const start = payload.items.length ? currentReportOffset + 1 : 0;
  const end = currentReportOffset + payload.items.length;
  elements.reportRange.textContent = `${start}–${end} of ${payload.total}`;
  const page = Math.floor(currentReportOffset / REPORT_PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(payload.total / REPORT_PAGE_SIZE));
  elements.reportPageLabel.textContent = `Page ${page} of ${pages}`;
  elements.reportPrevious.disabled = currentReportOffset === 0;
  elements.reportNext.disabled = currentReportOffset + REPORT_PAGE_SIZE >= payload.total;
}

function setReportLoading(isLoading) {
  elements.reportLoading.hidden = !isLoading;
  elements.reportFilterForm.setAttribute("aria-busy", String(isLoading));
  elements.reportPrevious.disabled = isLoading || currentReportOffset === 0;
  elements.reportNext.disabled = isLoading
    || currentReportOffset + REPORT_PAGE_SIZE >= currentReportTotal;
}

async function fetchReportTotal(query = "") {
  const suffix = query ? `&${query}` : "";
  const response = await fetch(`/api/v1/daily-reports?limit=1&offset=0${suffix}`, {
    headers: authHeaders(),
  });
  if (!response.ok) {
    return null;
  }
  return (await response.json()).total;
}

async function loadReportSummary() {
  const summaries = [
    [elements.reportTotal, ""],
    [elements.reportDeliveredTotal, "status=DELIVERED"],
    [elements.reportFailedTotal, "status=FAILED"],
    [elements.reportAiTotal, "ai_summary_used=true"],
  ];
  for (const [element] of summaries) {
    element.textContent = "—";
  }
  const results = await Promise.allSettled(
    summaries.map(([, query]) => fetchReportTotal(query)),
  );
  for (const [index, result] of results.entries()) {
    const total = result.status === "fulfilled" ? result.value : null;
    summaries[index][0].textContent = total === null ? "—" : String(total);
  }
}

async function loadReports() {
  setReportLoading(true);
  elements.reportError.hidden = true;
  const params = reportQueryFromFilters();
  updateReportUrl(params);
  try {
    const response = await fetch(`/api/v1/daily-reports?${params.toString()}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    renderReportPage(await response.json());
    void loadReportSummary();
    void loadReportSchedules();
    return true;
  } catch (error) {
    elements.reportResults.hidden = true;
    elements.reportEmpty.hidden = true;
    elements.reportError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Daily Reports";
    elements.reportError.hidden = false;
    return false;
  } finally {
    setReportLoading(false);
  }
}

function renderReportList(element, items) {
  element.replaceChildren(...items.map((item) => textElement("li", item)));
}

function renderReportDefinitions(element, fields) {
  element.replaceChildren(...fields.map(([term, value]) => definitionItem(term, String(value))));
}

function durationText(seconds) {
  if (seconds === null || seconds === undefined) {
    return "Unavailable";
  }
  if (seconds < 60) {
    return `${seconds.toFixed(1)} seconds`;
  }
  return `${(seconds / 60).toFixed(1)} minutes`;
}

function renderReportDetail(report) {
  const statistics = report.statistics;
  const ruleReport = report.rule_based_report;
  elements.reportDetailLabels.replaceChildren(
    reportStatusBadge(report.status),
    textElement("span", report.environment, "environment-chip"),
    textElement("span", report.ai_summary_used ? "AI-assisted" : "Rule-based", "source-badge"),
  );
  elements.reportDetailTitle.textContent = ruleReport.title;
  elements.reportDetailSubtitle.textContent = `${report.report_date} · ${statistics.timezone} · schema v${report.report_schema_version}`;
  elements.reportDetailFailures.textContent = String(statistics.failure_attempts);
  elements.reportDetailTasks.textContent = String(statistics.affected_task_instances);
  elements.reportDetailDagRuns.textContent = String(statistics.affected_dag_runs);
  elements.reportDetailUnresolved.textContent = String(statistics.incidents.unresolved);
  elements.reportDetailOverview.textContent = ruleReport.overview;
  renderReportList(elements.reportDetailHighlights, ruleReport.highlights);
  renderReportList(elements.reportDetailPriorities, ruleReport.priorities);
  document.querySelector("#report-top-failures-title").textContent = currentLanguage === "ko" ? "상위 실패 Task" : "Top failures";
  const topFailures = (statistics.top_failures || []).map((item) =>
    `${item.dag_id}.${item.task_id} · ${item.failure_count} · ${item.classification || "—"} · ${item.incident_id || "—"} ${item.incident_status || ""} · ${item.last_failed_at} · ${item.root_cause || "—"}`
  );
  if (!topFailures.length) {
    topFailures.push(statistics.failure_attempts ? (currentLanguage === "ko" ? "상위 실패 목록 미수집" : "Top failures not collected") : (currentLanguage === "ko" ? "실패 없음" : "No failures"));
  }
  renderReportList(document.querySelector("#report-top-failures"), topFailures);

  elements.reportDetailAi.hidden = report.ai_summary === null;
  if (report.ai_summary) {
    elements.reportDetailAiProvider.textContent = report.summary_provider || "AI";
    renderReportList(elements.reportDetailAiChanges, report.ai_summary.key_changes);
    renderReportList(elements.reportDetailAiPriorities, report.ai_summary.priorities);
  }

  renderReportDefinitions(elements.reportDeliveryFields, [
    ["Provider", report.provider],
    ["Status", report.status],
    ["Attempts", report.attempt_count],
    ["HTTP status", report.last_response_status ?? "—"],
    ["Last error", report.last_error_category ?? "—"],
    ["Delivered", report.delivered_at ? formatTimestamp(report.delivered_at).primary : "—"],
  ]);
  renderReportDefinitions(elements.reportIncidentFields, [
    ["New", statistics.incidents.new],
    ["Unresolved", statistics.incidents.unresolved],
    ["Recovered", statistics.incidents.recovered],
    ["New signatures", statistics.error_signatures.new],
    ["Repeated signatures", statistics.error_signatures.repeated],
    ["Mean recovery", durationText(statistics.mean_time.recovery_seconds)],
    ["Mean resolution", durationText(statistics.mean_time.resolution_seconds)],
  ]);
  renderReportDefinitions(
    elements.reportClassificationFields,
    Object.entries(statistics.classification_counts)
      .filter(([, count]) => count > 0)
      .map(([classification, count]) => [classification.replaceAll("_", " "), count]),
  );
  if (!elements.reportClassificationFields.children.length) {
    elements.reportClassificationFields.append(definitionItem("Failures", "No classified failures"));
  }
}

async function loadReportDetail(reportId) {
  currentReportId = reportId;
  elements.reportDetailBack.href = reportDetailBackHref();
  elements.reportDetailLoading.hidden = false;
  elements.reportDetailError.hidden = true;
  elements.reportDetailContent.hidden = true;
  try {
    const response = await fetch(`/api/v1/daily-reports/${encodeURIComponent(reportId)}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    renderReportDetail(await response.json());
    elements.reportDetailContent.hidden = false;
    return true;
  } catch (error) {
    elements.reportDetailError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Daily Report detail";
    elements.reportDetailError.hidden = false;
    return false;
  } finally {
    elements.reportDetailLoading.hidden = true;
  }
}

async function loadDiagnosisDetail(diagnosisId) {
  currentDiagnosisId = diagnosisId;
  elements.diagnosisDetailBack.href = diagnosisDetailBackHref();
  elements.diagnosisDetailLoading.hidden = false;
  elements.diagnosisDetailError.hidden = true;
  elements.diagnosisDetailContent.hidden = true;
  try {
    const response = await fetch(`/api/v1/diagnoses/${encodeURIComponent(diagnosisId)}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    const diagnosis = await response.json();
    renderDiagnosisDetail(diagnosis);
    elements.diagnosisDetailContent.hidden = false;
    void loadSimilarDiagnoses(diagnosis);
    return true;
  } catch (error) {
    elements.diagnosisDetailError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Diagnosis detail";
    elements.diagnosisDetailError.hidden = false;
    return false;
  } finally {
    elements.diagnosisDetailLoading.hidden = true;
  }
}

async function loadIncidents() {
  setLoading(true);
  elements.error.hidden = true;
  const params = queryFromFilters();
  updateUrl(params);
  try {
    const response = await fetch(`/api/v1/incidents?${params.toString()}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    renderPage(await response.json());
    await loadIncidentSummary(params);
    return true;
  } catch (error) {
    elements.results.hidden = true;
    elements.empty.hidden = true;
    elements.error.textContent = error instanceof Error ? error.message : "Unable to load incidents";
    elements.error.hidden = false;
    return false;
  } finally {
    setLoading(false);
  }
}

async function loadIncidentDetail(incidentId) {
  elements.detailBack.href = detailBackHref();
  elements.detailLoading.hidden = false;
  elements.detailError.hidden = true;
  elements.detailFeedback.hidden = true;
  elements.detailContent.hidden = true;
  try {
    const response = await fetch(`/api/v1/incidents/${encodeURIComponent(incidentId)}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    renderIncidentDetail(await response.json());
    return true;
  } catch (error) {
    elements.detailError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Incident detail";
    elements.detailError.hidden = false;
    return false;
  } finally {
    elements.detailLoading.hidden = true;
  }
}

function setTransitionSubmitting(isSubmitting) {
  elements.transitionForm.setAttribute("aria-busy", String(isSubmitting));
  elements.transitionClose.disabled = isSubmitting;
  elements.transitionCancel.disabled = isSubmitting;
  elements.transitionConfirm.disabled = isSubmitting;
}

async function submitOperatorTransition() {
  if (!pendingOperatorAction || !currentIncidentId || !currentIncidentStatus) {
    return;
  }

  const action = pendingOperatorAction;
  const incidentId = currentIncidentId;
  const body = {
    status: action.status,
    expected_status: currentIncidentStatus,
  };
  const reason = elements.transitionReason.value.trim();
  if (reason) {
    body.reason = reason;
  }

  setTransitionSubmitting(true);
  elements.transitionError.hidden = true;
  try {
    const response = await fetch(
      `/api/v1/incidents/${encodeURIComponent(incidentId)}/status`,
      {
        method: "PATCH",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (response.status === 409) {
      const detail = await errorDetail(response);
      closeTransitionDialog();
      if (await loadIncidentDetail(incidentId)) {
        showDetailFeedback(`${detail}. The Incident was refreshed; reconsider the current state.`);
      }
      return;
    }
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return;
      }
      throw new Error(detail);
    }

    closeTransitionDialog();
    if (await loadIncidentDetail(incidentId)) {
      showDetailFeedback(`Incident changed to ${action.status}.`);
    }
  } catch (error) {
    elements.transitionError.textContent = error instanceof Error
      ? error.message
      : "Unable to change the Incident state";
    elements.transitionError.hidden = false;
  } finally {
    setTransitionSubmitting(false);
  }
}

function showAdminError(message) {
  elements.adminError.textContent = message;
  elements.adminError.hidden = false;
  elements.adminFeedback.hidden = true;
}

function showAdminFeedback(message) {
  elements.adminFeedback.textContent = message;
  elements.adminFeedback.hidden = false;
  elements.adminError.hidden = true;
  elements.adminFeedback.focus();
}

async function adminApiRequest(url, options = {}) {
  const headers = { ...authHeaders(), ...(options.headers || {}) };
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    clearSession();
    showAuth(await errorDetail(response));
    return null;
  }
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return response;
}

function adminActionButton(label, action, className = "button button-secondary") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

async function updateManagedUser(userId, change, successMessage) {
  try {
    const response = await adminApiRequest(`/api/v1/admin/users/${encodeURIComponent(userId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    });
    if (response) {
      showAdminFeedback(successMessage);
      await loadAdminDashboard(false);
    }
  } catch (error) {
    showAdminError(error instanceof Error ? error.message : "Unable to update user");
  }
}

async function revokeManagedUserSessions(user) {
  try {
    const response = await adminApiRequest(
      `/api/v1/admin/users/${encodeURIComponent(user.id)}/revoke-sessions`,
      { method: "POST" },
    );
    if (response) {
      const result = await response.json();
      showAdminFeedback(`Revoked ${result.sessions_revoked} session(s) for ${user.email}.`);
      await loadAdminDashboard(false);
    }
  } catch (error) {
    showAdminError(error instanceof Error ? error.message : "Unable to revoke sessions");
  }
}

function openPasswordResetDialog(user) {
  passwordResetUser = user;
  elements.passwordResetCopy.textContent = `Set a temporary password for ${user.email}. All existing sessions will be revoked.`;
  elements.passwordResetValue.value = "";
  elements.passwordResetError.hidden = true;
  elements.passwordResetDialog.showModal();
  elements.passwordResetValue.focus();
}

function closePasswordResetDialog() {
  passwordResetUser = null;
  elements.passwordResetValue.value = "";
  elements.passwordResetDialog.close();
}

function renderAdminUsers(users) {
  elements.adminUserRows.replaceChildren();
  for (const user of users) {
    const row = document.createElement("tr");
    const identity = document.createElement("div");
    identity.className = "task-identity";
    identity.append(
      textElement("strong", user.display_name),
      textElement("span", user.email),
      ...(user.must_change_password
        ? [textElement("span", "Password change required", "muted-label")]
        : []),
    );
    appendCell(row, identity);

    const statusBadge = textElement("span", user.status, "status-badge");
    statusBadge.dataset.status = user.status;
    appendCell(row, statusBadge);

    const roleSelect = document.createElement("select");
    roleSelect.className = "admin-role-select";
    roleSelect.setAttribute("aria-label", `Role for ${user.email}`);
    for (const role of ["VIEWER", "OPERATOR", "ADMIN"]) {
      const option = document.createElement("option");
      option.value = role;
      option.textContent = role;
      option.selected = role === user.role;
      roleSelect.append(option);
    }
    appendCell(row, roleSelect);
    appendCell(row, user.last_login_at ? timestampBlock(user.last_login_at) : "Never");

    const actions = document.createElement("div");
    actions.className = "admin-actions";
    actions.append(
      adminActionButton("Save role", () => updateManagedUser(
        user.id,
        { role: roleSelect.value },
        `Updated role for ${user.email}.`,
      )),
      adminActionButton(
        user.status === "ACTIVE" ? "Disable" : "Enable",
        () => updateManagedUser(
          user.id,
          { status: user.status === "ACTIVE" ? "DISABLED" : "ACTIVE" },
          `${user.status === "ACTIVE" ? "Disabled" : "Enabled"} ${user.email}.`,
        ),
        user.status === "ACTIVE" ? "button button-danger" : "button button-secondary",
      ),
      adminActionButton("Reset password", () => openPasswordResetDialog(user)),
      adminActionButton("Revoke sessions", () => revokeManagedUserSessions(user)),
    );
    appendCell(row, actions);
    elements.adminUserRows.append(row);
  }
}

function configureConnectionForm(useDefaultApiUrl = false) {
  const providerName = elements.connectionProvider.value;
  const provider = MANAGED_CONNECTION_PROVIDERS[providerName];
  const isAirflow = providerName === "AIRFLOW";
  const isOllama = providerName === "OLLAMA";
  const isSlack = providerName === "SLACK";

  elements.connectionUiUrlGroup.hidden = !isAirflow;
  elements.connectionModelGroup.hidden = !isOllama;
  elements.connectionChannelGroup.hidden = !isSlack;
  elements.connectionSecretGroup.hidden = provider.secretField === null;
  elements.connectionModel.required = isOllama;
  elements.connectionChannel.required = isSlack;
  if (useDefaultApiUrl) {
    elements.connectionApiBaseUrl.value = provider.apiBaseUrl;
  }
  elements.connectionSecretLabel.textContent = isSlack ? "Bot token" : "Token";
}

function resetConnectionForm() {
  editingConnection = null;
  elements.connectionForm.reset();
  elements.connectionProvider.disabled = false;
  elements.connectionEnvironment.disabled = false;
  elements.connectionCancelEdit.hidden = true;
  elements.connectionSave.textContent = "Create connection";
  elements.connectionSecretHelp.textContent = "Write-only. Leave blank while editing to preserve the configured Secret.";
  configureConnectionForm(true);
}

function editManagedConnection(connection) {
  if (!MANAGED_CONNECTION_PROVIDERS[connection.provider]) {
    return;
  }
  editingConnection = connection;
  elements.connectionEnvironment.value = connection.environment;
  elements.connectionProvider.value = connection.provider;
  elements.connectionDisplayName.value = connection.display_name;
  elements.connectionApiBaseUrl.value = connection.non_secret_config.api_base_url || "";
  elements.connectionUiUrl.value = connection.non_secret_config.ui_base_url || "";
  elements.connectionModel.value = connection.non_secret_config.model || "";
  elements.connectionChannel.value = connection.non_secret_config.channel || "";
  elements.connectionSecret.value = "";
  elements.connectionProvider.disabled = true;
  elements.connectionEnvironment.disabled = true;
  elements.connectionCancelEdit.hidden = false;
  elements.connectionSave.textContent = "Save connection";
  elements.connectionSecretHelp.textContent = connection.secret_configured
    ? "Secret configured. Leave blank to preserve it, or enter a replacement."
    : "No Secret configured. Enter one to configure it.";
  configureConnectionForm();
  elements.connectionDisplayName.focus();
}

function managedConnectionRequestBody() {
  const providerName = editingConnection?.provider || elements.connectionProvider.value;
  const provider = MANAGED_CONNECTION_PROVIDERS[providerName];
  const nonSecretConfig = editingConnection
    ? { ...editingConnection.non_secret_config }
    : {};
  nonSecretConfig.api_base_url = elements.connectionApiBaseUrl.value.trim();
  delete nonSecretConfig.ui_base_url;
  delete nonSecretConfig.model;
  delete nonSecretConfig.channel;
  if (providerName === "AIRFLOW" && elements.connectionUiUrl.value.trim()) {
    nonSecretConfig.ui_base_url = elements.connectionUiUrl.value.trim();
  } else if (providerName === "OLLAMA") {
    nonSecretConfig.model = elements.connectionModel.value.trim();
  } else if (providerName === "SLACK") {
    nonSecretConfig.channel = elements.connectionChannel.value.trim();
  }
  const body = {
    environment: editingConnection?.environment || elements.connectionEnvironment.value.trim(),
    purpose: provider.purpose,
    provider: providerName,
    display_name: elements.connectionDisplayName.value.trim(),
    non_secret_config: nonSecretConfig,
    enabled: true,
  };
  if (editingConnection) {
    body.expected_version = editingConnection.version;
  }
  if (provider.secretField && elements.connectionSecret.value) {
    body.secret = { [provider.secretField]: elements.connectionSecret.value };
  }
  return body;
}

async function testManagedConnection(connection) {
  try {
    const response = await adminApiRequest(
      `/api/v1/admin/connections/${encodeURIComponent(connection.id)}/test`,
      { method: "POST" },
    );
    if (response) {
      const result = await response.json();
      const detail = result.error_category ? ` · ${result.error_category}` : "";
      showAdminFeedback(`Connection test ${result.status}${detail}.`);
      await loadAdminDashboard(false);
    }
  } catch (error) {
    showAdminError(error instanceof Error ? error.message : "Unable to test connection");
  }
}

async function disableManagedConnection(connection) {
  try {
    const response = await adminApiRequest(
      `/api/v1/admin/connections/${encodeURIComponent(connection.id)}/disable`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_version: connection.version }),
      },
    );
    if (response) {
      if (editingConnection?.id === connection.id) {
        resetConnectionForm();
      }
      showAdminFeedback(`Disabled ${connection.display_name}.`);
      await loadAdminDashboard(false);
    }
  } catch (error) {
    showAdminError(error instanceof Error ? error.message : "Unable to disable connection");
  }
}

function renderAdminConnections(connections) {
  elements.adminConnectionRows.replaceChildren();
  const hasConnections = connections.length > 0;
  elements.adminConnectionsEmpty.hidden = hasConnections;
  elements.adminConnectionsTable.hidden = !hasConnections;
  for (const connection of connections) {
    const row = document.createElement("tr");
    const identity = document.createElement("div");
    identity.className = "task-identity";
    identity.append(
      textElement("strong", connection.display_name),
      textElement("span", `${connection.environment} · ${connection.enabled ? "ENABLED" : "DISABLED"}`),
    );
    appendCell(row, identity);
    appendCell(row, `${connection.purpose} · ${connection.provider}`);

    const secret = document.createElement("div");
    secret.className = "connection-secret-state";
    secret.append(textElement(
      "span",
      connection.secret_configured ? "•••••• Configured" : "Not configured",
    ));
    appendCell(row, secret);

    const lastTest = document.createElement("div");
    lastTest.className = "task-identity";
    if (connection.last_test_status) {
      const badge = textElement("span", connection.last_test_status, "status-badge");
      badge.dataset.status = connection.last_test_status;
      lastTest.append(
        badge,
        textElement("span", connection.last_test_error_category || "No error"),
        textElement("span", formatTimestamp(connection.last_tested_at).primary),
      );
    } else {
      lastTest.append(textElement("span", "Never tested"));
    }
    appendCell(row, lastTest);

    const actions = document.createElement("div");
    actions.className = "admin-actions";
    const supported = Boolean(MANAGED_CONNECTION_PROVIDERS[connection.provider]);
    if (supported) {
      actions.append(adminActionButton("Edit", () => editManagedConnection(connection)));
    }
    if (supported && connection.enabled) {
      actions.append(adminActionButton("Test", () => testManagedConnection(connection)));
    }
    if (connection.enabled) {
      actions.append(adminActionButton(
        "Disable",
        () => disableManagedConnection(connection),
        "button button-danger",
      ));
    }
    appendCell(row, actions);
    elements.adminConnectionRows.append(row);
  }
}

function renderAdminAudit(events) {
  elements.adminAuditRows.replaceChildren();
  for (const event of events) {
    const row = document.createElement("tr");
    appendCell(row, timestampBlock(event.created_at));
    appendCell(row, event.actor_email || "System / recovery CLI");
    appendCell(row, event.action);
    appendCell(row, `${event.target_type} · ${event.target_id.slice(0, 8)}…`);
    appendCell(row, textElement("code", JSON.stringify(event.change_summary), "admin-summary"));
    elements.adminAuditRows.append(row);
  }
}

async function loadAdminDashboard(showLoading = true) {
  if (showLoading) {
    elements.adminLoading.hidden = false;
    elements.adminContent.hidden = true;
  }
  elements.adminError.hidden = true;
  try {
    const [usersResponse, connectionsResponse, auditResponse] = await Promise.all([
      adminApiRequest("/api/v1/admin/users?limit=200"),
      adminApiRequest("/api/v1/admin/connections?limit=200"),
      adminApiRequest("/api/v1/admin/audit-events?limit=100"),
    ]);
    if (!usersResponse || !connectionsResponse || !auditResponse) {
      return false;
    }
    const users = await usersResponse.json();
    const connections = await connectionsResponse.json();
    const audit = await auditResponse.json();
    elements.adminUserTotal.textContent = String(users.total);
    elements.adminConnectionTotal.textContent = String(connections.total);
    renderAdminUsers(users.items);
    renderAdminConnections(connections.items);
    renderAdminAudit(audit.items);
    elements.adminContent.hidden = false;
    return true;
  } catch (error) {
    showAdminError(error instanceof Error ? error.message : "Unable to load user administration");
    return false;
  } finally {
    elements.adminLoading.hidden = true;
  }
}

async function loadCurrentView() {
  if (currentUser?.must_change_password) {
    showPasswordChange();
    return false;
  }
  const params = new URLSearchParams(window.location.search);
  const reportId = params.get("report");
  if (reportId) {
    showConnectedView("report-detail");
    return loadReportDetail(reportId);
  }
  const diagnosisId = params.get("diagnosis");
  if (diagnosisId) {
    showConnectedView("diagnosis-detail");
    return loadDiagnosisDetail(diagnosisId);
  }
  const signatureId = params.get("signature");
  if (signatureId) {
    showConnectedView("signature-detail");
    return loadSignatureDetail(signatureId);
  }
  const incidentId = params.get("incident");
  if (incidentId) {
    showConnectedView("detail");
    return loadIncidentDetail(incidentId);
  }
  if (params.get("view") === "signatures") {
    currentSignatureId = null;
    showConnectedView("signatures");
    return loadSignatures();
  }
  if (params.get("view") === "diagnoses") {
    currentDiagnosisId = null;
    showConnectedView("diagnoses");
    return loadDiagnoses();
  }
  if (params.get("view") === "reports") {
    currentReportId = null;
    showConnectedView("reports");
    return loadReports();
  }
  if (params.get("view") === "admin" && storedSession().role === "admin") {
    showConnectedView("admin");
    return loadAdminDashboard();
  }
  currentIncidentId = null;
  currentIncidentStatus = null;
  showConnectedView("dashboard");
  return loadIncidents();
}

elements.transitionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitOperatorTransition();
});

elements.transitionClose.addEventListener("click", closeTransitionDialog);
elements.transitionCancel.addEventListener("click", closeTransitionDialog);
elements.transitionDialog.addEventListener("cancel", (event) => {
  if (elements.transitionConfirm.disabled) {
    event.preventDefault();
    return;
  }
  pendingOperatorAction = null;
  elements.transitionError.hidden = true;
});

elements.humanDiagnosisForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitHumanDiagnosis();
});
elements.humanDiagnosisClose.addEventListener("click", closeHumanDiagnosisDialog);
elements.humanDiagnosisCancel.addEventListener("click", closeHumanDiagnosisDialog);

elements.adminCreateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(elements.adminCreateForm);
  const body = Object.fromEntries(data.entries());
  try {
    const response = await adminApiRequest("/api/v1/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (response) {
      const user = await response.json();
      elements.adminCreateForm.reset();
      showAdminFeedback(`Created ${user.email}.`);
      await loadAdminDashboard(false);
    }
  } catch (error) {
    showAdminError(error instanceof Error ? error.message : "Unable to create user");
  }
});

elements.connectionProvider.addEventListener("change", () => configureConnectionForm(true));
elements.connectionCancelEdit.addEventListener("click", resetConnectionForm);
elements.connectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const connectionId = editingConnection?.id || generateUuid();
    const response = await adminApiRequest(
      `/api/v1/admin/connections/${encodeURIComponent(connectionId)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(managedConnectionRequestBody()),
      },
    );
    if (response) {
      const saved = await response.json();
      resetConnectionForm();
      showAdminFeedback(`Saved ${saved.display_name}.`);
      await loadAdminDashboard(false);
    }
  } catch (error) {
    showAdminError(error instanceof Error ? error.message : "Unable to save connection");
  }
});

elements.passwordResetForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!passwordResetUser) {
    return;
  }
  const user = passwordResetUser;
  try {
    const response = await adminApiRequest(
      `/api/v1/admin/users/${encodeURIComponent(user.id)}/reset-password`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ temporary_password: elements.passwordResetValue.value }),
      },
    );
    if (response) {
      closePasswordResetDialog();
      showAdminFeedback(`Reset the temporary password for ${user.email}.`);
      await loadAdminDashboard(false);
    }
  } catch (error) {
    elements.passwordResetError.textContent = error instanceof Error
      ? error.message
      : "Unable to reset password";
    elements.passwordResetError.hidden = false;
  }
});

elements.passwordResetClose.addEventListener("click", closePasswordResetDialog);
elements.passwordResetCancel.addEventListener("click", closePasswordResetDialog);
elements.passwordResetDialog.addEventListener("cancel", () => {
  passwordResetUser = null;
  elements.passwordResetValue.value = "";
});

elements.authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = elements.accessEmail.value.trim();
  const password = elements.accessPassword.value;
  if (!email || !password) {
    showAuth("Enter your email and password.");
    return;
  }
  elements.authError.hidden = true;
  setAuthLoading(true);
  try {
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      showAuth(await errorDetail(response));
      return;
    }
    const payload = await response.json();
    currentUser = payload.user;
    elements.accessPassword.value = "";
    if (currentUser.must_change_password) {
      showPasswordChange();
      return;
    }
    await loadCurrentView();
  } catch {
    showAuth("Unable to reach DagSentry.");
  } finally {
    setAuthLoading(false);
  }
});

elements.changePasswordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const currentPassword = elements.currentPassword.value;
  const newPassword = elements.newPassword.value;
  if (newPassword !== elements.confirmNewPassword.value) {
    elements.changePasswordError.textContent = translatedText("Passwords do not match.");
    elements.changePasswordError.hidden = false;
    return;
  }
  elements.changePasswordError.hidden = true;
  try {
    const response = await fetch("/api/v1/auth/change-password", {
      method: "POST",
      headers: {
        ...authHeaders(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    if (!response.ok) {
      elements.changePasswordError.textContent = await errorDetail(response);
      elements.changePasswordError.hidden = false;
      return;
    }
    clearSession();
    showAuth(translatedText("Password changed. Sign in again."));
  } catch {
    elements.changePasswordError.textContent = translatedText("Unable to reach DagSentry.");
    elements.changePasswordError.hidden = false;
  }
});

elements.disconnect.addEventListener("click", async () => {
  const response = await fetch("/api/v1/auth/logout", {
    method: "POST",
    headers: authHeaders(),
  });
  if (!response.ok && response.status !== 401) {
    elements.sessionStatus.textContent = await errorDetail(response);
    return;
  }
  clearSession();
  elements.rows.replaceChildren();
  elements.signatureRows.replaceChildren();
  elements.signatureOccurrenceRows.replaceChildren();
  elements.diagnosisRows.replaceChildren();
  elements.reportRows.replaceChildren();
  elements.adminUserRows.replaceChildren();
  elements.adminConnectionRows.replaceChildren();
  elements.adminAuditRows.replaceChildren();
  resetConnectionForm();
  showAuth();
});

elements.sidebarToggle.addEventListener("click", () => {
  setSidebarCollapsed(!sidebarCollapsed);
});

elements.primaryNavigation.addEventListener("click", async (event) => {
  const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
  if (
    !link
    || event.button !== 0
    || event.metaKey
    || event.ctrlKey
    || event.shiftKey
    || event.altKey
  ) {
    return;
  }
  event.preventDefault();
  history.pushState({}, "", link.href);
  setFiltersFromUrl();
  setSignatureFiltersFromUrl();
  setDiagnosisFiltersFromUrl();
  setReportFiltersFromUrl();
  window.scrollTo(0, 0);
  await loadCurrentView();
});

elements.filterForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  currentOffset = 0;
  await loadIncidents();
});

elements.refreshIncidents.addEventListener("click", async () => {
  await loadIncidents();
});

elements.clearFilters.addEventListener("click", async () => {
  elements.filterForm.reset();
  revealAdvancedFilters(elements.filterForm);
  document.querySelector("#status-filter").value = "OPEN";
  currentOffset = 0;
  await loadIncidents();
});

elements.signatureFilterForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  currentSignatureOffset = 0;
  await loadSignatures();
});

elements.clearSignatureFilters.addEventListener("click", async () => {
  elements.signatureFilterForm.reset();
  revealAdvancedFilters(elements.signatureFilterForm);
  currentSignatureOffset = 0;
  await loadSignatures();
});

elements.diagnosisFilterForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  currentDiagnosisOffset = 0;
  await loadDiagnoses();
});

elements.clearDiagnosisFilters.addEventListener("click", async () => {
  elements.diagnosisFilterForm.reset();
  currentDiagnosisOffset = 0;
  await loadDiagnoses();
});

elements.reportFilterForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  currentReportOffset = 0;
  await loadReports();
});

elements.reportScheduleForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveReportSchedule();
});

for (const control of [
  elements.reportScheduleTime,
  elements.reportScheduleTimezone,
  elements.reportScheduleEnabled,
]) {
  control.addEventListener("change", renderReportSchedulePreview);
}

elements.reportManualRunForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await requestManualReportRun();
});

elements.clearReportFilters.addEventListener("click", async () => {
  elements.reportFilterForm.reset();
  currentReportOffset = 0;
  await loadReports();
});

elements.previous.addEventListener("click", async () => {
  currentOffset = Math.max(0, currentOffset - PAGE_SIZE);
  await loadIncidents();
});

elements.next.addEventListener("click", async () => {
  if (currentOffset + PAGE_SIZE < currentTotal) {
    currentOffset += PAGE_SIZE;
    await loadIncidents();
  }
});

elements.signaturePrevious.addEventListener("click", async () => {
  currentSignatureOffset = Math.max(0, currentSignatureOffset - SIGNATURE_PAGE_SIZE);
  await loadSignatures();
});

elements.signatureNext.addEventListener("click", async () => {
  if (currentSignatureOffset + SIGNATURE_PAGE_SIZE < currentSignatureTotal) {
    currentSignatureOffset += SIGNATURE_PAGE_SIZE;
    await loadSignatures();
  }
});

elements.diagnosisPrevious.addEventListener("click", async () => {
  currentDiagnosisOffset = Math.max(0, currentDiagnosisOffset - DIAGNOSIS_PAGE_SIZE);
  await loadDiagnoses();
});

elements.diagnosisNext.addEventListener("click", async () => {
  if (currentDiagnosisOffset + DIAGNOSIS_PAGE_SIZE < currentDiagnosisTotal) {
    currentDiagnosisOffset += DIAGNOSIS_PAGE_SIZE;
    await loadDiagnoses();
  }
});

elements.reportPrevious.addEventListener("click", async () => {
  currentReportOffset = Math.max(0, currentReportOffset - REPORT_PAGE_SIZE);
  await loadReports();
});

elements.reportNext.addEventListener("click", async () => {
  if (currentReportOffset + REPORT_PAGE_SIZE < currentReportTotal) {
    currentReportOffset += REPORT_PAGE_SIZE;
    await loadReports();
  }
});

async function changeOccurrencePage(offset) {
  currentOccurrenceOffset = Math.max(0, offset);
  const params = new URLSearchParams(window.location.search);
  if (currentOccurrenceOffset === 0) {
    params.delete("occurrence_offset");
  } else {
    params.set("occurrence_offset", String(currentOccurrenceOffset));
  }
  history.replaceState({}, "", `/ui/?${params.toString()}`);
  if (currentSignatureId) {
    await loadSignatureDetail(currentSignatureId);
  }
}

elements.occurrencePrevious.addEventListener("click", async () => {
  await changeOccurrencePage(currentOccurrenceOffset - OCCURRENCE_PAGE_SIZE);
});

elements.occurrenceNext.addEventListener("click", async () => {
  if (currentOccurrenceOffset + OCCURRENCE_PAGE_SIZE < currentOccurrenceTotal) {
    await changeOccurrencePage(currentOccurrenceOffset + OCCURRENCE_PAGE_SIZE);
  }
});

elements.signatureTrend7.addEventListener("click", async () => {
  await refreshSignatureTrend(7);
});

elements.signatureTrend30.addEventListener("click", async () => {
  await refreshSignatureTrend(30);
});

const languageObserver = new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    for (const node of mutation.addedNodes) {
      translateSubtree(node);
    }
  }
});
languageObserver.observe(document.body, { childList: true, subtree: true });

elements.languageSelect.addEventListener("change", async () => {
  currentLanguage = elements.languageSelect.value;
  localStorage.setItem(LANGUAGE_STORAGE_KEY, currentLanguage);
  applyLanguage();
  if (storedSession().token) {
    await loadCurrentView();
  } else {
    showAuth();
  }
  applyLanguage();
});

elements.timezoneSelect.addEventListener("change", async () => {
  currentTimezone = elements.timezoneSelect.value;
  localStorage.setItem(TIMEZONE_STORAGE_KEY, currentTimezone);
  if (storedSession().token) {
    await loadCurrentView();
  }
});

window.addEventListener("popstate", async () => {
  setFiltersFromUrl();
  setSignatureFiltersFromUrl();
  setDiagnosisFiltersFromUrl();
  setReportFiltersFromUrl();
  if (storedSession().token) {
    await loadCurrentView();
  }
});

async function restoreSession() {
  try {
    const response = await fetch("/api/v1/auth/me");
    if (!response.ok) {
      clearSession();
      showAuth();
      return;
    }
    currentUser = await response.json();
    if (currentUser.must_change_password) {
      showPasswordChange();
      return;
    }
    await loadCurrentView();
  } catch {
    clearSession();
    showAuth("Unable to reach DagSentry.");
  } finally {
    elements.sessionLoading.hidden = true;
  }
}

applyLanguage();
setSidebarVisibility(false);
resetConnectionForm();
setFiltersFromUrl();
setSignatureFiltersFromUrl();
setDiagnosisFiltersFromUrl();
setReportFiltersFromUrl();
restoreSession();
