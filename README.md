# KISA 보안공지 카톡 알리미

KISA 보호나라의 [보안공지 RSS](https://www.boho.or.kr/kr/rss.do?bbsId=B0000133)를 GitHub Actions로 5분마다 확인하여 새로운 공지가 업로드 될 경우 카카오톡 나와의 채팅으로 알림을 전송합니다. 별도 서버나 DB는 사용하지 않습니다.

## 비용과 동작 특성

- 이 저장소를 **Public**으로 만들고 표준 Linux runner를 쓰면 GitHub Actions 실행은 무료입니다.
- GitHub Actions 스케줄의 최단 주기는 5분입니다. 이 워크플로는 매시 `02, 07, 12, …, 57`분(UTC)에 실행되어, 정각 대기열을 피합니다. (정각 부근에 지연이나 누락 건 발생 가능)
- 예약 실행은 지연되거나 드물게 누락될 수 있으므로 정확히 5분 이내 전달을 보장하지는 않습니다.
- KISA 공지 ID와 월간 heartbeat만 `data/state.json`에 커밋합니다. 카카오 토큰·키는 절대 저장소에 저장하지 않습니다.
- 공개 저장소에서 60일간 활동이 없으면 GitHub가 스케줄을 끌 수 있어, 새 공지가 없어도 30일마다 heartbeat를 커밋합니다.

## 1. 카카오 개발자 설정

1. [Kakao Developers](https://developers.kakao.com/)에서 앱을 만듭니다.
2. **카카오 로그인**을 ON으로 설정합니다.
3. Redirect URI에 아래 주소를 정확히 등록합니다.

   ```text
   http://localhost:4312/callback
   ```

4. 동의항목에서 **카카오톡 메시지 전송**(`talk_message`)을 사용 설정합니다.
5. **제품 링크 관리**에 웹 도메인 `https://www.boho.or.kr`를 등록합니다. 알림의 `공지 열기` 버튼에 필요합니다.
6. REST API 키와 카카오 로그인 Client Secret을 확인합니다. Client Secret은 기본적으로 사용 설정되어 있으므로 끄지 않는 것을 권합니다.

## 2. 최초 refresh token 발급

아래는 Windows PowerShell 예시입니다. 값은 화면·셸 기록에도 남을 수 있으므로 가능한 한 개인 PC에서 실행하고, 작업이 끝난 뒤 터미널을 닫으세요.

```powershell
$env:KAKAO_REST_API_KEY = "카카오_REST_API_키"
$env:KAKAO_CLIENT_SECRET = "카카오_Client_Secret"
python scripts/get_kakao_refresh_token.py
```

브라우저에서 카카오 로그인과 `talk_message` 동의를 완료하면 스크립트가 `KAKAO_REFRESH_TOKEN` 값 하나를 출력합니다. 이 값은 다음 단계의 GitHub Secret에만 넣습니다.

## 3. GitHub Actions Secrets 등록

저장소 **Settings → Secrets and variables → Actions**에서 다음 repository secrets를 만듭니다.

| Secret | 값 |
| --- | --- |
| `KAKAO_REST_API_KEY` | 카카오 앱의 REST API 키 |
| `KAKAO_CLIENT_SECRET` | 카카오 로그인 Client Secret |
| `KAKAO_REFRESH_TOKEN` | 앞 단계에서 발급한 refresh token |
| `GH_SECRETS_PAT` | 다음 단계에서 발급하는, 한 저장소 한정 GitHub PAT |

`GH_SECRETS_PAT`는 카카오가 refresh token을 교체해 줄 때만 `KAKAO_REFRESH_TOKEN` Secret을 자동으로 새 값으로 저장하는 데 사용합니다. 없이도 처음에는 동작하지만, refresh token 교체 시점에는 자동 실행이 중단됩니다.

PAT는 GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens**에서 만들고 다음처럼 최소화합니다.

- Repository access: 이 저장소 한 개만 선택
- Repository permissions: **Secrets — Read and write**
- 만료일: 가능한 짧게 정하고, 만료 전에 새 토큰으로 Secret을 교체

## 4. 테스트와 운영 시작

1. **Actions → KISA security notices to KakaoTalk → Run workflow**를 실행합니다.
2. 처음 실행은 기존 공지를 보내지 않고 현재 RSS를 기준 상태로 저장합니다.
3. 같은 화면에서 `Send a KakaoTalk connection-test message`를 체크해 다시 실행합니다.
4. 카카오톡 **나와의 채팅**에 연결 테스트 메시지가 오면 완료입니다.

휴대폰의 카카오톡 및 운영체제 알림에서 `나와의 채팅` 알림이 허용되어 있어야 푸시를 받을 수 있습니다.

## 트러블 슈팅 가이드

- `KOE006`: Redirect URI가 정확히 일치하지 않습니다. 프로토콜, 포트, 경로까지 `http://localhost:4312/callback`과 같아야 합니다.
- `talk_message` 권한 오류: 카카오 개발자 콘솔에서 동의항목을 켜고, 최초 토큰 발급을 다시 하세요.
- `gh exited ...`: `GH_SECRETS_PAT` 권한이 부족하거나 만료됐습니다. 위의 최소 권한으로 새 PAT를 발급해 Secret을 교체하세요.
- Action이 멈춤: Public repository의 스케줄이 비활성화됐는지 Actions 페이지에서 확인하고 Enable workflow를 누르세요.