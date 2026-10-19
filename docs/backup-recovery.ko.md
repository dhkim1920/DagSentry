# 백업, 복원, 비상 Admin 복구

이 runbook은 PostgreSQL에 저장한 local user, browser session, Admin audit history, 암호화 Managed Connection을
다룹니다. Airflow metadata나 external Provider 상태는 백업하지 않습니다. DB backup에는 password/session/CSRF hash,
audit history, 암호화 Provider Secret이 있으므로 plaintext credential이 없어도 민감 정보로 취급합니다.

`DAGSENTRY_CONNECTION_ENCRYPTION_KEY`는 PostgreSQL에 저장하지 않습니다. 해당 key와 version은 Secret manager에서
DB backup과 별도로 보관하고, 복원은 DB와 같은 key version을 하나의 recovery unit으로 사용합니다. command argument,
shell history, filename, log, ticket에 DB/Admin password, Provider Secret, encryption key를 넣지 말고 backup/restore 중
connection key rotation을 하지 않습니다.

backup은 restricted PostgreSQL service identity와 encrypted access-controlled location을 사용합니다. application/Git/
Alembic revision, UTC time, key version(키 자체 제외)을 기록하고 migration/key rotation을 막은 뒤 restrictive umask,
custom-format `pg_dump`, `pg_restore --list`, checksum으로 만들고 Secret manager의 같은 key/version을 별도 확인합니다.

복구 가능 선언 전 isolated PostgreSQL에서 backup을 test restore합니다. 같은 DagSentry version과 matching key/version으로
Alembic revision, Admin login/audit, Managed Connection metadata, read-only connection test를 검사하고 verification DB와
temporary Secret copy를 파기합니다. production restore는 API/Worker/scheduled job/Airflow collector를 중지하고 기존
DB를 덮지 않는 새 empty DB에 restore/verify한 뒤 matching key를 구성하고 필요한 forward migration만 적용합니다.
API readiness/Admin login을 먼저 확인하고 Worker/scheduled job을 관찰하며 이전 DB는 recovery 확인까지 보관합니다.

`uv run dagsentry-backup --directory /secure/backups --service dagsentry-backup --key-version 1
--git-revision <배포-커밋-SHA>`로 자동 백업을 생성할 수 있습니다. PostgreSQL client 도구와 libpq service/pass
파일이 필요합니다. private 디렉터리에서 custom dump와 목록 검사를 마친 뒤 SHA-256, application/Git/Alembic
revision, UTC time, key version을 담은 `manifest.json`과 함께 게시합니다. 기존 백업은 삭제하지 않습니다.
결과의 `restore_verified: false`는 실제 복원이 아직 확인되지 않았다는 뜻입니다. 복원 전 checksum을 비교하고
위 격리 복원 절차를 수행합니다. [정리 명령과 systemd timer](maintenance.md)로 정기 실행할 수 있습니다.

영문 원문: [Backup, restore, and emergency Admin recovery](backup-recovery.md)
