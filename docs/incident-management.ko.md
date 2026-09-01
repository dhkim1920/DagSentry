# Incident 연관과 수명 주기

DagSentry는 Diagnosis 저장 뒤 Notification 전송 전에 성공적으로 진단한 Failure Event를 Incident에 연관합니다.
초기 identity는 `environment + dag_id + task_id + error_signature_id`입니다. `OPEN`, `ACKNOWLEDGED`만 활성
연관 대상이며, 일치 Failure는 활성 Incident에 연결되고 `RECOVERED`, `RESOLVED`, `IGNORED` 뒤의 이후 Failure는
새 Incident를 만듭니다. partial unique index가 concurrent Worker에서도 identity별 활성 Incident를 하나만 보장합니다.
`UNSIGNABLE` Failure는 신뢰할 grouping key가 없어 각각 별도 Incident가 됩니다.

상태는 `OPEN`, `ACKNOWLEDGED`, `RECOVERED`, `RESOLVED`, `IGNORED`입니다. `OPEN`은 어느 상태로든, `ACKNOWLEDGED`
는 recovered/resolved/ignored로, `RECOVERED`는 resolved/ignored로 갈 수 있으며 resolved/ignored는 terminal입니다.
acknowledge/resolve/ignore는 operator만 수행하고 system은 활성 Incident만 recovered로 표시할 수 있습니다. AI는
Incident state를 바꾸지 않으며 같은 상태 반복은 idempotent no-op입니다.

새 활성 Incident의 첫 Failure만 전체 Notification을 받습니다. 이후 Failure의 Diagnosis는 저장하지만 Notification은
`SUPPRESSED`가 되어 remote Provider를 호출하지 않습니다. payload snapshot에는 Incident ID, 현재 상태, 연결 Failure
수를 넣으며 periodic/count reminder는 없습니다.

## 운영자 API

Viewer/Operator/Admin session은 Incident 조회, Operator/Admin은 PATCH transition을 수행합니다. 목록은 status,
environment, DAG/task, pagination, allowlisted sort/order를 받고 optimistic concurrency를 위해 update request에
`expected_status`가 필요합니다. stale update는 `409 Conflict`입니다. 실제 전이는 actor, initiator, timestamp,
previous/next state, optional reason을 같은 transaction으로 감사합니다.

영문 원문: [Incident correlation and lifecycle](incident-management.md)
