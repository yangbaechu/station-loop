# Station Loop — 우주정거장 에이전트 실험실

승무원 3명이 고장 난 우주정거장에서 협력하는 턴 기반 시뮬레이터.
사용자는 무전으로 지시하거나 행동을 직접 지정하고, 가능한 행동과 거절 이유를 관찰한다.
**LLM은 제안, Wirelog는 행동 전제 판정, Python은 세계 상태와 자원 갱신**을 담당한다.

첫 MVP에는 구역 3개, 승무원 3명, 산소·전력·수리 부품과 최대 12턴의 시나리오가 있다.
브라우저 지도·무전·수동 행동 선택, 실행 기록 저장·내보내기·리플레이를 제공한다.

## 바로 실행

Python 3.12 이상과 uv가 필요하다. 프로젝트 루트에서 환경을 준비한다.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python 'pyrewire==1.1.3' 'PyJWT[crypto]>=2.10,<3'
```

서버 실행:

```bash
.venv/bin/python -m station_loop serve
```

브라우저에서 **http://127.0.0.1:8765**를 연다. 기본은 실제 **Wirelog 판정 + 오프라인 협력 규칙 승무원**이다.
“다음 턴”을 누르거나 하단에서 승무원 행동을 직접 지정할 수 있다.
오프라인 승무원은 LLM이 아니며 자연어 지시를 해석하지 않는다. 화면에도 이를 표시한다.

처음에는 **샘플 플레이 관전하기**를 누르고 **다음 장면**으로 진행해 보자.
정해진 8턴 동안 부품 확보·운반·전달·수리를 설명과 함께 볼 수 있다.
로그인이나 LLM 호출 없이 실제 게임 규칙으로 진행한다. **샘플 처음부터**로 다시 보거나
**직접 플레이로 돌아가기**로 기존 승무원 모드의 새 게임을 시작할 수 있다.

새 환경에서는 다음과 같이 준비한다.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python 'pyrewire==1.1.3'
```

지원 wheel은 호환 libwirelog를 포함한다. 지원되지 않는 플랫폼의 소스 설치는
[PyreWire 지원 문서](https://github.com/semantic-reasoning/PyreWire/blob/3ab87f7560c9312f585f02da1b0c6c5ce1a4da06/docs/support.md)를 따른다.
상위 저장소 복제는 앱 실행의 필수 조건이 아니다.

## ChatGPT 로그인으로 LLM 승무원 사용

일반 터미널에서 기존 서버를 종료한 뒤 재시작한다.

```bash
.venv/bin/python -m station_loop serve --agents chatgpt
```

1. http://127.0.0.1:8765 에서 **Continue with ChatGPT**를 누른다.
2. 공식 로그인 화면에서 계정·워크스페이스와 ChatGPT 구독 사용 권한을 확인하고 승인한다.
3. 돌아온 화면에서 사용 가능한 모델을 선택하고 “다음 턴”을 진행한다.

API 키는 필요 없다. 로그인만 승인하고 구독 사용 권한을 승인하지 않았다면 호출하지 않는다.
이 경로의 요청은 연결한 ChatGPT 플랜/사용 가능한 크레딧을 사용한다. 제공 여부는
계정·플랜·워크스페이스와 OpenAI의 현재 정책에 따라 달라진다.
사용량 한도에 도달하면 턴을 확정하지 않고 **Manage usage**로 안내한다.
기존 API 키로 자동 전환하지 않는다.

로그인 패널은 기본 `serve` 모드에서도 제공된다. 모델을 선택하면 ChatGPT 승무원으로
전환하며 새 게임을 시작한다. 계정 변경도 인증을 완료한 후 새 게임을 시작한다.
다른 계정을 추가하거나 저장된 계정을 다시 연결할 수 있고, 같은 이메일의 다른
워크스페이스 등록은 서로 구분한다.

인증정보는 `.station-loop/auth/`에 저장한다. Unix 폴더 권한은 `0700`, 파일은 `0600`이며
Git에서 제외한다. 브라우저·실행 기록·내보내기에는 토큰을 전달하지 않는다.
로그아웃은 갱신 세션 해제를 시도한 뒤 로컬 토큰을 지우고, 호스트 ID와 계정/클라이언트
등록 정보는 재연결을 위해 유지한다. 서버 해제를 확인하지 못하면 화면에 알린다.
기존 Codex/ChatGPT 앱의 인증 파일을 읽거나 재사용하지 않는다.

한 인증 폴더는 한 서버 프로세스만 사용한다. 저장 위치를 바꾸려면
`--auth-dir /원하는/경로`를 지정한다. 로그인 토큰 갱신을 지원하는 현재 구현은 Unix용이다.

새 환경에서 로그인 의존성 설치:

```bash
uv pip install --python .venv/bin/python 'PyJWT[crypto]>=2.10,<3'
```

구현·제약·공식 근거는 [ChatGPT 연결 문서](docs/chatgpt-login.md)에 있다.
작성자는 로컬 환경에서 ChatGPT 연결의 실제 실행을 확인했다.
자동 테스트는 서명된 테스트 토큰과 가짜 OAuth·SSE 서버 응답을 사용한다.
모델별 게임 성공률을 비교하는 반복 실험은 아직 수행하지 않았다.

## API 키로 LLM 승무원 사용

OpenAI Responses API와 Structured Outputs를 사용한다. API 키는 서버 프로세스 환경변수로만
읽고 브라우저나 실행 기록에 넣지 않는다. `.env` 파일을 자동으로 읽지는 않는다.

```bash
# 환경에 OPENAI_API_KEY와 사용할 STATION_LOOP_MODEL을 설정한 뒤:
.venv/bin/python -m station_loop serve --agents openai --model "$STATION_LOOP_MODEL"
```

이 모드에서 사용자 지시를 대상 승무원에게 전달한다. 한 턴에 승무원별 최대 한 번,
총 3회 호출하며 호출당 타임아웃은 30초다. 자동 재시도는 하지 않는다.
구조화 응답 오류·거절·통신 실패는 해당 승무원의 대기와 오류 이벤트로 기록한다.
실제 API에는 사용량 요금이 발생한다.

필요하면 `OPENAI_BASE_URL`로 Responses 호환 서버의 `/v1` 기본 URL을 지정한다.
호환 서버의 Structured Outputs 지원 여부는 별도로 확인해야 한다.
모델 ID는 자동 선택하지 않는다.

API 계약 근거: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
현재 라이브 API 호출은 검증하지 않았고, 요청·응답 처리는 가짜 transport로 테스트했다.

## 터미널 데모와 리플레이

```bash
# 고정 기준 플레이: 실제 Wirelog에서 8턴 복구 확인
.venv/bin/python -m station_loop demo

# 현재 상태를 보고 결정하는 오프라인 협력 승무원
.venv/bin/python -m station_loop demo --agents cooperative

# 위 명령이 출력한 실행 폴더로 검증. LLM은 다시 호출하지 않는다.
.venv/bin/python -m station_loop replay runs/<run-id>
```

Wirelog를 설치할 수 없는 환경에서는 `--rules reference`를 **명시**하면 Python 참조 판정을 쓴다.
Wirelog 초기화·평가 실패 시 자동으로 참조 판정으로 전환하지 않는다.

```bash
python3 -m station_loop serve --rules reference
```

실행 기록은 `runs/<run-id>/metadata.json`과 턴별 JSON이다. 완료 턴만 원자적으로 저장한다.
리플레이는 초기 상태·규칙/판정 어댑터 버전·이벤트·상태 해시를 대조한다.
미완료 턴에 대한 LLM 호출 복구와 crash-safe 외부 효과 처리는 제공하지 않는다.
Python에서는 `station_loop.replay.resume_run()`으로 마지막 완료 턴을 복구할 수 있다.

## 검증

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check station_loop/static/app.js
```

검증 범위는 [검증 기록](docs/verification.md)에 있다. 실제 Wirelog 판정, 자원 경쟁,
종료 조건, 저장 실패 롤백, 리플레이와 HTTP 처리기를 확인했다.
현재 환경은 소켓 생성과 외부 API 통신을 제한하므로 실제 브라우저 렌더링과 라이브 LLM 호출은
여기서 확인하지 못했다. 일반 터미널에서 서버를 실행해야 한다.

## 문서와 상위 저장소

- [제품·시스템 설계](docs/design.md)
- [시나리오 데이터](scenarios/life_support.json)
- [기준 플레이와 인수 조건](docs/acceptance.md)
- [상위 프로젝트와 버전 근거](docs/upstream.md)

```bash
bash scripts/clone-upstreams.sh
```

위 스크립트는 Wirelog와 PyreWire를 `vendor/`에 복제하고 검사한 커밋으로 고정한다.
최신 소스를 따로 살펴보려면 `--inspect-main`을 붙인다.
현재 환경에서는 GitHub DNS 조회가 실패해 복제를 완료하지 못했다.

아직 구현하지 않은 항목: 비교 실험용 검증 생략 모드, 숨겨진 정보·장기 기억, 추가 시나리오.
상위 저장소 수정·PR 제출은 수행하지 않았다.

## 라이선스

애플리케이션 소스는 [MIT 라이선스](LICENSE)로 제공한다.
Wirelog·PyreWire 및 다른 의존성은 각 프로젝트의 라이선스를 따른다.
