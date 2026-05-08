# 백로그

당장 진행하지 않지만 방향성을 잡아두는 아이템들.

---

## [아키텍처] allocation 설정 DB 이관 + Google Sheets를 UI 미들웨어로

### 현재 구조
```
사용자 → Google Sheets (allocation 비율 직접 편집) → Python (읽어서 리밸런싱)
```

### 목표 구조
```
사용자 → Google Sheets (편집) → DB (source of truth) → Python (리밸런싱)
                   ↑                    ↓
              변경 감지/sync        allocation 읽기
```

### 상세 아이디어
- allocation 목표 비중을 DB(BigQuery 또는 별도 DB)가 단일 진실 출처(source of truth)로 관리
- Google Sheets는 사용자가 비율을 보고 편집하는 UI 역할 (Python이 직접 읽지 않음)
- Sheets 변경 → DB sync 트리거 필요 (Apps Script, Sheets webhook, 또는 주기적 sync)
- Python은 DB에서만 allocation 읽음

### 장점
- allocation 이력 추적 가능 (언제 비율을 바꿨는지)
- Sheets가 유일한 진입점이 아니므로 나중에 앱/웹으로 대체 가능
- 잘못된 Sheets 편집이 즉각 반영되지 않으므로 버퍼 역할 (검증 로직 추가 가능)

### 미래 확장
- Google Sheets → 모바일 앱 또는 웹 UI로 대체 가능
- allocation 변경 시 알림(Slack 등) 연동 가능
- 다중 포트폴리오 / 다중 사용자 지원 가능성

### 고려 사항
- Sheets → DB sync 방식 결정 필요 (push vs pull, 주기, 충돌 처리)
- DB 선택: BigQuery(분석 최적화, 실시간 업데이트 비효율) vs Cloud Firestore/PostgreSQL(CRUD 최적)
- 현재 인프라(GCP 기반)에서는 Firestore 또는 Cloud SQL이 적합할 수 있음
