# 버전 호환성과 deprecation 정책

이 문서는 repository의 실행 검사로 뒷받침되는 호환성만 정의합니다. 현재 DagSentry는 untagged `0.3.0.dev0`
개발 라인이므로 package version뿐 아니라 immutable image digest 또는 Git commit으로 배포 build를 식별해야 합니다.
`Tested`는 PR CI에서 실행함, `Declared`는 packaging range만 허용함, `Upgrade source`는 forward DB upgrade만
시험함을 뜻합니다. 목록에 없는 조합은 암묵 호환이 아니라 unsupported입니다.

현재 CI 대상은 Python 3.11/3.12, PostgreSQL 17, Airflow `>=3.1.8,<4`(정확한 3.1.8과 3.3.1),
Linux Docker Compose v2.20+ single-host deployment, Alembic `base -> 0020`, v0.2 schema `0008`에서 current head로의
forward upgrade를 포함합니다. application, API, Worker, scheduled job, Airflow package, migration은 같은 build에서
와야 하며 mixed-version process는 시험하지 않습니다. SQLite는 bounded unit test만 쓰며 production DB가 아닙니다.

`frontend-browser` CI job은 Playwright 1.58.2/Chromium으로 데모 UI를 검사합니다. Firefox/Safari와 실제 기기 인증은
완료되지 않았으며 배포할 커밋의 workflow 결과를 별도로 확인해야 합니다.

production upgrade는 backup·encryption key 보존, writer 중지/quiesce, forward migration 한 번 적용, 같은 build의
모든 process 교체, readiness 및 bounded functional check 뒤 재개 순서입니다. Alembic downgrade는 production rollback
계약이 아니며 실패 시 pre-upgrade DB와 이전 image/key를 복원합니다.

공개 호환성 표면은 문서화한 HTTP/CLI/env/Secret/Compose/Airflow entry point/persisted payload 계약입니다. 0.x에서
minor release는 incompatible change를 포함할 수 있고, deprecation한 `0.N` 표면은 `0.(N+1)` 내내 제공하며
`0.(N+2)`보다 이르게 제거하지 않습니다. 1.0 이후 지원 public surface 제거는 다음 major에서만 가능합니다.

영문 원문: [Version compatibility and deprecation policy](version-compatibility.md)
