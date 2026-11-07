# MVP 검증 기록

확인일: 2026-11-07. Python 3.12, PyreWire 1.1.3 / 번들 Wirelog 0.70.0.

## 공개 결과물

2026-11-07 [yangbaechu/station-loop](https://github.com/yangbaechu/station-loop)를 공개 저장소로 게시했다.
GitHub API에서 공개 상태와 main 브랜치의 소스·문서·테스트·MIT 라이선스를 확인했다.
인증정보와 개인 실행 기록은 업로드 목록에서 제외했다. 별도의 웹 서비스 배포는 수행하지 않았다.
작성자는 로컬 환경에서 ChatGPT 연결의 실제 실행도 확인했다. 모델별 반복 성능 비교는 아직 수행하지 않았다.

## 실제 실행한 범위

- 실제 Wirelog로 `rules/eligibility.dl`을 실행하고, 8턴 기준 플레이의 자원·아이템·승리 상태 확인.
- Python 참조 엔진과 실제 Wirelog에 각각 같은 19개 상태 전이·경계 사례를 적용.
- 역할·위치·공구·부품·전력 검사, 다중 거절 이유, 간접 도달성과 직접 이동 구분.
- 아이템 경쟁과 회전 우선순위, 대사의 물리 상태 비변경, 실패 응답의 대기 처리.
- 환경 갱신·사망/성공 우선순위·시간 초과, 중복 요청, 저장/엔진 오류 시 턴 전체 롤백.
- 저장 후 리플레이, 완료 턴 복구, 변조된 기록의 거절.
- OpenAI 구조화 요청·응답 파싱·거절 처리·타깃별 지시 전달을 가짜 transport로 확인.
- HTTP 처리기에 실제 HTTP 요청 바이트를 메모리에서 전달해 정적 파일·턴·새 게임·내보내기 확인.
- JavaScript 구문 검사.

실행 명령:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m station_loop demo --rules wirelog
node --check station_loop/static/app.js
```

최종 결과(ChatGPT 로그인·샘플 관전 포함): **79개 중 76개 통과, 환경 제약으로 TCP 테스트 3개 skip**.
샘플 모드 전환·행동 변경 거절·8턴 승리·처음부터 재시작·이전 승무원 모드 복귀를 메모리 HTTP 처리기로 확인했다.
JavaScript 구문 검사와 Python 컴파일 검사도 통과했다.
실제 Wirelog를 사용하는 메모리 HTTP 처리기에서도 8턴 플레이·승리·내보내기를 확인했다.
별도 CLI 실행에서는 협력 승무원이 8턴에 산소 12, 전력 7로 성공했고, 저장 기록의 CLI 리플레이도 통과했다.

## 환경 때문에 확인하지 못한 범위

ChatGPT 로그인 추가 검증: RSA 서명 토큰의 issuer/audience/만료/nonce, state·PKCE,
재인증·계정 분리·토큰 교체·로그아웃, 구독 권한 거절, SSE 중단·한도 오류,
HTTP callback·모델 선택을 가짜 OAuth/JWKS/SSE 응답으로 확인했다.
작성자는 로컬 환경에서 ChatGPT 연결의 실제 실행을 확인했다고 보고했다.
이 자동 검증 환경에서 실제 계정 OAuth·추론을 재현한 결과와는 구분한다.

- 실제 TCP 서버 연결 테스트 3개: 소켓 생성이 `PermissionError`로 거절되어 명시적으로 skip.
  같은 HTTP 처리기는 별도 메모리 테스트에서 통과했다. 실제 서버 연결 통과로 세지 않는다.
- 실제 브라우저의 레이아웃·클릭 상호작용: 브라우저와 서버 소켓 사용이 불가능해 미확인.
- 실제 OpenAI 호출의 자동 검증: 현재 환경의 외부 네트워크 제한으로 미실행.
  API 키가 있다는 사실만으로 라이브 호출이 검증됐다고 주장하지 않는다.
- Wirelog 소스 빌드·클론: DNS 제한으로 미완료. 설치한 wheel의 코어를 사용했다.

## 설계에서 바뀐 점

초안은 배치 평가를 제안했으나 실제 PyreWire 1.1.3에서 `BatchProgram`의 빈 관계 CSV 읽기가
실패하고 문자열 결과가 intern ID로 노출되는 것을 확인했다. 따라서 매 판정에 새로운
**EasySession + snapshot**을 사용한다. 공개 `intern()`으로 사실과 규칙의 문자열을 등록해
결과를 역변환한다. step/snapshot을 혼용하지 않고 세션을 매번 닫는다.

이는 전체 상태의 새 평가다. 증분 계산 성능이나 자동 설명 추적 기능을 구현했다고 주장하지 않는다.
실행 설명은 고정 reason code와 상태에서 앱이 작성한다.
