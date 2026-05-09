# 백로그

당장 진행하지 않지만 방향성을 잡아두는 아이템들.

---

## [Phase B follow-up] Codex 리뷰에서 보류 처리한 항목

### 부분 체결 정확도 강화 (P1 #3)
- 현재: 시장가 ETF는 즉시 100% 체결이 일반적이라 `filled_quantity = transaction_qty if rt_cd=='0' else 0`으로 충분.
- 개선: 별도 체결조회 API(`uapi/domestic-stock/v1/trading/inquire-daily-ccld` 등)로 실제 체결 수량을 정확히 확인해 부분 체결도 반영.
- 우선순위: 낮음. 시장가 ETF에서 부분 체결 사례 발견 시 진행.

### `transaction_quantity` deprecated alias 제거
- 현재: `requested_quantity` / `filled_quantity` (ARCH-004) 도입했으나 `transaction_quantity`를 alias = `filled_quantity`로 유지 (외부 수신부 호환).
- 개선: 3개월 유예 후 (2026-08 이후) alias 제거. BigQuery `trade_log`에서 컬럼 자체 제거는 schema_update_options로 자동 처리되지 않으므로 수동 ALTER 또는 view로 마이그레이션.
- 우선순위: 중간. Slack/BigQuery/Sheets 외부 수신부에서 `transaction_quantity` 참조 코드 모두 제거된 시점에 진행.

### KIS HTTP `tr_cont` 누락을 KISAPIError로 표준화 (P2 #7)
- 현재: KIS 응답에 `tr_cont` 헤더가 없으면 raw KeyError로 노출 (`src/kis/client.py` 페이지네이션 경로).
- 개선: ARCH-005 `_check_rt_cd`와 동일하게 `KISAPIError` 도메인 예외로 변환.
- 우선순위: 낮음. 현재 raw KeyError로도 운영 진단에 충분.

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
