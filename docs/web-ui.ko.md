# Web UI

DagSentry Web UI는 운영자가 Incident, Error Signature, Diagnosis, Notification delivery, Managed Connection을
안전하게 살펴보고 권한이 있는 상태 전이를 수행하는 interface입니다. Viewer는 읽기만, Operator는 허용된 Incident
전이, Admin은 사용자와 Managed Connection 관리를 수행합니다. 브라우저 session은 HttpOnly cookie와 CSRF 보호를
사용하며 API token은 legacy/automation 용도로 분리됩니다.

UI는 raw Task log, credential, Provider response body를 노출하지 않습니다. 목록은 pagination과 allowlisted filter/
sort를 사용하고 detail은 마스킹된 evidence와 감사 가능한 상태 이력을 보여 줍니다. screen 구성, information hierarchy,
product requirement, verification은 관련 한국어/영문 설계 문서를 참조하세요.

영문 원문: [Web UI](web-ui.md)
