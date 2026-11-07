# ChatGPT 구독 연결

2026-11-07 공식 문서를 기준으로 구현했다. 로컬 개인 프로젝트용 공개 클라이언트 흐름이다.
유료 서비스·원격 호스팅에 대한 허용을 이 구현으로 추정하지 않는다.

## 흐름

1. 실행 중인 로컬 HTTP 서버가 `/auth/callback`을 수신한다.
2. 앱 전용 호스트 ID를 처음 한 번 생성하고 재사용한다.
3. 새 연결은 `dynamic_agent_client`로 등록한다. 기존 연결은 발급받은 클라이언트 ID를 재사용한다.
4. 매 시도에 별도 state·nonce·PKCE를 생성한다. 로그인은 공식 OpenAI 페이지에서 이루어진다.
5. 반환된 state와 클라이언트 ID를 확인하고, 같은 callback URI와 verifier로 코드를 교환한다.
6. OpenID discovery의 JWKS로 ID 토큰 서명·issuer·audience·만료·nonce를 검증한다.
7. 검증한 subject와 발급된 클라이언트를 계정 등록에 함께 보관한다. 이메일은 표시용이다.
8. 토큰 응답의 구독 사용 scope를 확인하고 계정별 모델 목록을 가져온다.
9. 사용자가 고른 모델로 Responses SSE를 호출하며 완료 이벤트까지 읽는다.

## 인증과 저장

- 앱 자체 인증 저장소를 사용한다. Codex의 `auth.json`, 브라우저 쿠키, 기존 앱 세션을 사용하지 않는다.
- 인증 파일과 호스트 ID는 `.station-loop/auth/accounts.json`에 owner-only 권한으로 원자적으로 쓴다.
- 파일 잠금으로 같은 저장소를 여러 프로세스가 동시에 갱신하지 못하게 한다.
- 초기 ID 토큰 검증에는 PyJWT와 cryptography를 사용한다. 대칭 서명이나 unsigned JWT는 허용하지 않는다.
- 계정 전환은 선택한 등록의 클라이언트로 새 OAuth를 완료한 뒤 적용한다. 실패하면 이전 계정/게임을 유지한다.
- 재인증 URL에 ID 토큰 힌트를 넣지 않는다. 계정 선택은 브라우저에서 명시적으로 진행한다.
- 유효 상태의 callback은 1회만 처리하며, 취소·만료·잘못된 응답은 활성 계정을 바꾸지 않는다.
- 갱신은 직렬화하고 교체된 갱신 토큰과 접근 토큰을 함께 저장한다.
- 갱신 권한이 무효화되면 토큰을 지우고 재연결을 안내한다. 일시적 통신 오류는 등록을 삭제하지 않는다.
- 로그아웃 때 discovery의 revocation endpoint로 해제를 시도한다. 원격 확인 실패 시 로컬 토큰은 지우고 이를 사용자에게 알린다.
- callback URL을 로그에 남기지 않고, 성공/실패 후 쿼리가 없는 `/`로 리다이렉트한다.

## 모델 호출

- API 키 모드와 별도 provider다. ChatGPT 모드에서는 `OPENAI_API_KEY`와 `OPENAI_BASE_URL`을 사용하지 않는다.
- 구독 권한이 없는 ID 로그인만으로 호출하지 않는다.
- 계정별 모델 목록에서 표시 가능한 모델만 선택지로 제공한다. 모델명을 추측해 기본 선택하지 않는다.
- 요청은 공식 public Responses 경로를 사용한다. ChatGPT 내부 backend-api 경로를 사용하지 않는다.
- `store=false`, `stream=true`, 배열 형식 input을 사용한다. 이 경로에서 지원하지 않는 출력 토큰 상한 필드는 보내지 않는다.
- 부분 응답이나 `[DONE]`만으로 성공 처리하지 않는다. 완료 이벤트의 상태까지 확인한다.
- 사용량 초과·권한 문제는 호출을 중지하며, 이미 생성된 행동 제안만으로 턴을 확정하지 않는다.
- SSE에서 실패·중단이 발생하면 세계 상태와 자원을 유지한다. 정상 응답의 잘못된 행동 형식은 기존 규칙대로 해당 캐릭터의 대기가 된다.
- 모델 응답과 토큰 사용량은 기존 실행 기록에 들어가지만 인증정보와 비공개 reasoning 항목은 들어가지 않는다.

## 구현 상태

- 구현: 로그인 패널, 계정 추가/재연결, 계정별 모델 선택, 구독 사용 확인 대화상자, 사용량 관리 링크.
- 구현: OAuth/서명 검증/보관/갱신/로그아웃, SSE 전용 provider, 권한·사용량 오류 시 턴 중단.
- 검증: 실제 RSA 서명 토큰을 쓰는 단위 테스트, 가짜 OAuth/JWKS/모델 목록/SSE, HTTP callback 처리기.
- 작성자 확인: 로컬 환경에서 ChatGPT 연결의 실제 실행을 확인했다.
- 추가 확인 필요: 여러 모델의 반복 실행 결과, 샘플 관전 등 새 UI의 전체 브라우저 동작.
  자동 검증 환경은 외부 네트워크·TCP 서버 소켓이 제한되어 있어 실제 계정 인증을 재현하지 않았다.

## 공식 근거

- [등록과 로그인](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [계정과 토큰 갱신](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [모델 목록과 추론](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [구독 경로의 지원 범위](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [UI/UX](https://developers.openai.com/siwc/ui-ux-guidelines)

공식 preview 계약이 바뀌면 이 문서와 테스트도 함께 갱신한다.
