# Managed Connection

Managed Connection은 Airflow Connection 객체가 아닌 DagSentry 소유 outbound 설정입니다. 환경마다 목적별 연결은
하나만 둘 수 있습니다. Provider Secret은 AES-256-GCM으로 암호화해 저장하며 32 random byte의 Base64 key와 양의
key version을 설정합니다. key는 deployment Secret mechanism으로만 주입하고 Git, DB, image에 넣지 않습니다.

Admin API는 session을 요구하며 list/detail/create-update/disable/read-only test endpoint를 제공합니다. 응답은
`secret_configured`만 보여 주고 Secret, ciphertext, nonce, key version은 반환하지 않습니다. connection update에는
`expected_version`으로 optimistic concurrency를 적용하고 명시적 Secret만 새 nonce로 교체합니다. Webhook/Teams/
Discord URL은 authentication token을 포함할 수 있어 Secret field입니다.

database configuration source를 택하면 Airflow, Ollama, Slack은 환경별 immutable snapshot을 job/run마다 load하고
환경값 fallback 없이 fail closed합니다. UI의 Secret field는 write-only이며 blank edit는 기존 암호문을 보존합니다.
지원 read-only test는 Airflow version, Ollama tags, Slack auth/channel info이며 message/notification을 보내지 않습니다.
key rotation은 maintenance window에 모든 Secret을 한 transaction에서 새 key/fresh nonce로 re-encrypt합니다.

영문 원문: [Managed Connections](managed-connections.md)
