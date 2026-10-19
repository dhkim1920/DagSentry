# Managed Connection

Managed Connection은 Airflow Connection 객체가 아닌 DagSentry 소유 outbound 설정입니다. 환경마다 목적별 연결은
하나만 둘 수 있습니다. Provider Secret은 AES-256-GCM으로 암호화해 저장하며 32 random byte의 Base64 key와 양의
key version을 설정합니다. key는 deployment Secret mechanism으로만 주입하고 Git, DB, image에 넣지 않습니다.

Admin API는 session을 요구하며 list/detail/create-update/disable/read-only test endpoint를 제공합니다. 응답은
`secret_configured`만 보여 주고 Secret, ciphertext, nonce, key version은 반환하지 않습니다. connection update에는
`expected_version`으로 optimistic concurrency를 적용하고 명시적 Secret만 새 nonce로 교체합니다. Webhook/Teams/
Discord URL은 authentication token을 포함할 수 있어 Secret field입니다.

database configuration source를 택하면 모든 내장 LLM·알림 Provider와 Airflow는 환경별 immutable snapshot을 job/run마다 load하고
환경값 fallback 없이 fail closed합니다. UI의 Secret field는 write-only이며 blank edit는 기존 암호문을 보존합니다.
지원 read-only test는 Airflow version, Ollama tags, Slack auth/channel info이며 message/notification을 보내지 않습니다.
key rotation은 maintenance window에 모든 Secret을 한 transaction에서 새 key/fresh nonce로 re-encrypt합니다.

영문 원문: [Managed Connections](managed-connections.md)

OpenAI·Azure OpenAI·Anthropic·Bedrock·Webhook·Teams·Discord·SMTP도 Admin UI에서 편집하고
DB 연결로 실행할 수 있습니다. Bedrock은 암호화한 AWS key와 선택적 session token만 사용합니다.
SMTP는 `0021` migration을 먼저 적용하며, 인증이 필요하면 username/password를 함께 입력합니다.
AWS key나 SMTP 인증처럼 여러 필드로 된 Secret을 교체할 때는 전체 필드를 다시 입력합니다.
SMTP 연결이 남아 있으면 `0020` downgrade는 데이터를 보존하기 위해 거부합니다.

외부 연결 화면의 **연결 설정 선택 상태 (API 기준)**에서 목적별 출처(환경변수/DB)와 API 기본 환경의
선택 Provider·연결 버전 또는 활성 연결 누락을 확인합니다. 저장된 연결에는 `저장됨`, `API 설정 기준
선택됨`, `비활성`을 구분해 표시합니다. 연결을 저장하는 것만으로 설정 출처가 바뀌지는 않으며,
화면에서 `DAGSENTRY_*_CONFIG_SOURCE=database` 설정과 프로세스 재시작 방법을 안내합니다.

이 표시는 API의 로컬 선택 설정이며 Worker·스케줄러의 실제 배포 설정이나 연결 성공을 확인한 결과는
아닙니다. 사용하는 프로세스에 같은 출처·암호화 키를 설정하고 환경변수를 변경한 프로세스를 재시작해야
합니다. 진행 중인 작업은 기존 스냅샷을 유지하며 새 작업부터 최신 DB 연결을 읽습니다.
선택 상태 조회는 Provider 생성·외부 요청·복호화를 하지 않으며, 암호화된 DB 연결 선택 시 API에 키가
없으면 설정 안내를 표시합니다. 키의 유효성이나 연결 성공 여부는 별도 테스트로 확인해야 합니다.
