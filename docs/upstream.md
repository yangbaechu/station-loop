# 상위 프로젝트 조사와 버전 선택

확인일: 2026-11-07. 아래는 GitHub 연결 도구로 읽은 원본 근거다.
직접 `git clone`은 터미널 DNS 오류(`Could not resolve host: github.com`)로 실패했다.
현재 `vendor/`에 완성된 저장소는 없다. 이후 사용자가 설치한 PyreWire 1.1.3 wheel과
번들 Wirelog 0.70.0으로 실제 규칙과 게임 코어를 실행했다. 소스 빌드는 수행하지 않았다.
아래는 최초 설계 조사 내용이며 구현에서의 API 선택 변경은 `verification.md`를 참고한다.

## 저장소와 고정 커밋

| 목적 | 저장소 | 커밋 |
|---|---|---|
| MVP의 Python 연동 기준 | semantic-reasoning/PyreWire | `3ab87f7560c9312f585f02da1b0c6c5ce1a4da06` |
| 위 PyreWire가 번들·검증하는 Wirelog | semantic-reasoning/wirelog | `c353350232c35356b34085abf5780a2fcb81e80f` |
| 조사 시점 Wirelog main, 별도 참고용 | semantic-reasoning/wirelog | `cbdd4da940d55e9806c4e06f78d7b48e4c29a674` |

PyreWire의 조사 버전은 1.1.3이며 `docs/support.md`와 `pyproject.toml`의 wheel 설정이
Wirelog v0.70.0의 위 커밋을 지정한다. 최신 main과 조합했을 때의 동작은 검증하지 않았다.
최신 main의 새 API가 MVP에 필요하다고 가정하지 않는다.

## 읽은 근거

- [PyreWire 지원 범위](https://github.com/semantic-reasoning/PyreWire/blob/3ab87f7560c9312f585f02da1b0c6c5ce1a4da06/docs/support.md): CPython 3.11~3.15, 지원 wheel에는 libwirelog 포함. 소스 설치에는 별도 호환 라이브러리가 필요.
- [PyreWire 패키지 설정](https://github.com/semantic-reasoning/PyreWire/blob/3ab87f7560c9312f585f02da1b0c6c5ce1a4da06/pyproject.toml): 1.1.3 및 번들 코어 커밋.
- [BatchProgram과 Result 구현](https://github.com/semantic-reasoning/PyreWire/blob/3ab87f7560c9312f585f02da1b0c6c5ce1a4da06/src/pyrewire/batch.py): `from_string`, `optimize`, `evaluate`, `Result.relation`, 명시적 자원 해제.
- [공개 Python import](https://github.com/semantic-reasoning/PyreWire/blob/3ab87f7560c9312f585f02da1b0c6c5ce1a4da06/src/pyrewire/__init__.py): `BatchProgram`, `EasySession` 등이 공개됨.
- [Wirelog README](https://github.com/semantic-reasoning/wirelog/blob/cbdd4da940d55e9806c4e06f78d7b48e4c29a674/README.md): C11, 재귀 Datalog, 변경분 처리, 임베딩 방식.
- [Wirelog 의미 모델](https://github.com/semantic-reasoning/wirelog/blob/cbdd4da940d55e9806c4e06f78d7b48e4c29a674/docs/SEMANTICS.md): 삽입·삭제의 Z-set 중복도, 실행 설정별 재평가, 외부 부수 효과 책임.
- [Wirelog 문법](https://github.com/semantic-reasoning/wirelog/blob/cbdd4da940d55e9806c4e06f78d7b48e4c29a674/docs/SYNTAX.md): 관계 선언, 재귀, 계층화된 부정, 집계 제약.
- [PyreWire 모드 문서](https://github.com/semantic-reasoning/PyreWire/blob/3ab87f7560c9312f585f02da1b0c6c5ce1a4da06/docs/semantics/step-vs-snapshot.md): 같은 세션에서 step/snapshot 혼용 제한. 본문 일부 예제와 설명의 모드 확정 시점이 일치하는지는 구현 때 확인할 것.

## 설계에 반영한 경계

1. MVP는 Python 3.12와 PyreWire 1.1.3을 사용한다. 배치 API를 먼저 확인한 뒤 실제 판정은 `EasySession`으로 구현했다.
2. 매 판정에 현재 사실을 넣어 새 snapshot 평가를 한다. MVP에서 증분 평가 성능을 주장하지 않는다.
3. 부정·정수 비교·재귀 도달성의 코어/래퍼 호환성을 실제 테스트로 확인했다.
4. LLM 생성 문자열을 그대로 Datalog에 이어 붙이지 않는다. ID는 호스트가 생성하고 문자열은
   인코더에서 이스케이프한다. 런타임 규칙은 사람이 검토한 고정 파일만 사용한다.
5. 정렬 순서와 set 의미는 호스트에서 정규화한다. 다중 경로로 유도된 사실의 중복도를
   게임의 추가 자원이나 추가 행동으로 해석하지 않는다.
6. 결과를 읽은 후 `EasySession`의 context manager로 자원을 해제한다.
   원본 C 포인터를 앱 계층으로 노출하지 않는다.
7. Wirelog에는 계산만 맡긴다. LLM 호출, 저장, 자원 차감, 알림 등 부수 효과는 호스트 책임이다.

## 다음 환경 확인

- 지원 wheel의 실제 설치와 `import pyrewire`를 확인했다.
- 설치된 패키지 버전과 실제 로드한 코어 버전을 실행 기록에 보관한다. 라이브러리 경로의 기록은 미구현.
- wheel 번들 라이브러리를 쓸 때 임의의 `WIRELOG_LIB`로 최신 main을 덮어씌우지 않음.
- 소스 빌드 시 Meson/Ninja·C 컴파일러와 하위 의존성의 다운로드 가능 여부 확인.
- 고정 코어에서 `rules/eligibility.dl`과 8턴 기준 플레이를 실제 실행했다.

## 라이선스

Wirelog와 PyreWire는 서로 라이선스 구성이 다르므로 원본 LICENSE를 각각 확인한다.
상위 프로젝트 코드를 수정하거나 배포할 때 해당 고지와 의무를 검토한다.
본 앱의 라이선스 선택·공개는 후속 작업이다. 원본 저장소에 기여하는 경우 그 저장소의
CLA 절차를 따른다. 이번 작업에서는 상위 프로젝트 수정·CLA 동의·PR 제출을 수행하지 않는다.
