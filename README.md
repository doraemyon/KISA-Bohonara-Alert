# KISA 보안공지 Telegram 알리미

KISA 보호나라의 [보안공지 RSS](https://www.boho.or.kr/kr/rss.do?bbsId=B0000133)를 확인해 새 공지를 Telegram 봇으로 즉시 알립니다. 별도 서버나 데이터베이스는 필요하지 않습니다.

```text
cron-job.org (매분) → GitHub Actions → KISA RSS 확인 → Telegram 푸시
                           ↑
                 GitHub 자체 cron (5분, 비상용)
```

## 가장 중요한 알림 속도

- GitHub Actions의 자체 `schedule` 이벤트는 최단 설정이 5분이지만 실제로는 수 시간 지연되거나 누락될 수 있습니다.
- 빠른 알림을 위해 **cron-job.org가 매분 GitHub의 `workflow_dispatch` API를 호출**하도록 설정합니다.
- 정상 상황에서는 KISA RSS에 공지가 나타난 후 대략 1~3분 안에 Telegram 알림을 목표로 합니다. 외부 서비스와 네트워크를 사용하므로 절대적인 시간 보장은 불가능합니다.
- GitHub 자체 5분 cron도 비상용으로 남아 있어 외부 스케줄러가 잠시 실패했을 때 보조합니다.

## 비용과 보안

- 이 저장소가 **Public**이고 표준 Linux runner를 사용하면 GitHub Actions 실행은 무료입니다.
- cron-job.org와 Telegram Bot API도 이 용도에서는 무료로 사용할 수 있습니다.
- `data/state.json`에는 공개된 KISA 공지 ID와 월간 heartbeat만 저장합니다.
- Telegram bot token과 GitHub PAT는 절대 저장소, Issue, 채팅에 올리지 말고 지정된 Secret 입력란에만 넣으세요.

## 1. Telegram 봇 만들기

1. 휴대폰에 Telegram을 설치하고 로그인합니다.
2. Telegram에서 공식 계정 **`@BotFather`**를 검색해 대화를 시작합니다.
3. `/newbot`을 보내고 안내에 따라 봇 표시 이름과 사용자 이름을 정합니다.
   - 사용자 이름은 `bot`으로 끝나야 합니다. 예: `my_kisa_alert_bot`
4. BotFather가 발급한 **HTTP API token**을 복사합니다. 이 값은 비밀번호와 같습니다.
5. 새로 만든 봇을 검색해 대화방을 열고 **Start**를 누르거나 `/start`를 보냅니다.

## 2. Telegram 수신 대상 설정

개인 대화로 받을 때는 아래 방법으로 숫자 chat ID를 확인합니다. 공개 채널로 여러 사람과 공유할 때는 봇을 채널 관리자로 추가하고 `TELEGRAM_CHAT_ID`에 숫자 대신 `@채널사용자이름`을 사용하면 됩니다.

이 저장소 폴더에서 PowerShell을 열고 실행합니다.

```powershell
$env:TELEGRAM_BOT_TOKEN = "BotFather가_발급한_HTTP_API_token"
python scripts/get_telegram_chat_id.py
```

다음처럼 출력되는 숫자를 복사합니다.

```text
Use this value for the TELEGRAM_CHAT_ID GitHub secret:
123456789
```

아무 숫자도 나오지 않으면 봇 대화방에서 `/start`를 보낸 뒤 다시 실행하세요.

## 3. GitHub Actions Secrets 등록

GitHub 저장소 → **Settings → Secrets and variables → Actions → New repository secret**에서 두 개를 만듭니다.

| Secret 이름 | 값 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | BotFather가 발급한 HTTP API token |
| `TELEGRAM_CHAT_ID` | 개인 대화의 숫자 ID 또는 공개 채널의 `@채널사용자이름` |

이전에 사용하던 다른 알림 서비스의 Secrets는 이 버전에서 사용하지 않으므로 삭제해도 됩니다.

## 4. Telegram 테스트

1. GitHub 저장소 상단의 **Actions** 탭을 엽니다.
2. 왼쪽에서 **KISA security notices to Telegram**을 선택합니다.
3. 오른쪽 **Run workflow**를 누릅니다.
4. `Send a Telegram connection-test message`를 체크합니다.
5. 초록색 **Run workflow** 버튼을 누릅니다.

실행 결과가 초록 체크이고 Telegram에 아래 메시지가 오면 연결 성공입니다.

```text
✅ KISA 보안공지 Telegram 연결이 정상입니다.
```

휴대폰 팝업이 보이지 않으면 Telegram 앱의 **설정 → 알림 및 소리 → 개인 채팅**을 켜고, 해당 봇 대화방이 음소거되지 않았는지 확인하세요.

## 5. 매분 실행하는 빠른 스케줄러 설정

GitHub 자체 cron의 수 시간 지연을 피하기 위한 필수 단계입니다.

### 5-1. 최소 권한 GitHub PAT 만들기

1. GitHub 우측 상단 프로필 → **Settings**
2. **Developer settings → Personal access tokens → Fine-grained tokens**
3. **Generate new token** 선택
4. Repository access에서 **Only select repositories**를 선택하고 `KISA-Bohonara-Alert`만 지정
5. Repository permissions에서 **Actions → Read and write**만 허용
6. 만료일을 정하고 토큰을 생성해 복사

이 토큰은 cron-job.org가 이 워크플로를 시작하는 용도로만 사용합니다. GitHub Actions Secret에는 넣을 필요가 없습니다.

### 5-2. cron-job.org 작업 만들기

1. [cron-job.org](https://cron-job.org/)에서 무료 계정을 만들고 **Create cronjob**을 누릅니다.
2. 아래처럼 입력합니다. (URL 부분은 본인 저장소에 맞게 변경 필요)

| 항목 | 값 |
| --- | --- |
| Title | `KISA Telegram Alert Trigger` |
| URL | `https://api.github.com/repos/doraemyon/KISA-Bohonara-Alert/actions/workflows/kisa-telegram.yml/dispatches` |
| Schedule | Every minute |

3. **ADVANCED* 탭에서 다음을 설정합니다. (Authorization 필드에 아까 생성했던 PAT 값 입력 필요)
 - Header 부분은 Key와 Value 부분을 나눠서 입력 필요함.
 - EX) Key: Content-Type / Value: application/json

| 항목 | 값 |
| --- | --- |
| Request method | `POST` |
| Request body | `{"ref":"main"}` |
| Header | `Accept: application/vnd.github+json` |
| Header | `Authorization: Bearer 본인의_GitHub_PAT값` |
| Header | `X-GitHub-Api-Version: 2026-03-10` |
| Header | `Content-Type: application/json` |

4. 저장한 뒤 **Test run**을 누릅니다.
5. GitHub 저장소의 **Actions** 탭에 `workflow_dispatch` 실행이 나타나면 성공입니다.

PAT가 만료되면 외부 호출이 중단되므로 만료 전에 교체하세요. cron-job.org에서 실패 알림 이메일도 켜 두는 것을 권장합니다.

## 동작 방식

- 처음 실행할 때는 기존 공지를 모두 발송하지 않고 현재 RSS 항목을 기준 상태로 저장합니다.
- 이후 RSS에 새로운 ID가 나타나면 Telegram으로 제목과 `공지 열기` 버튼을 보냅니다.
- 공지 포맷터 확장이 연결돼 있으면 첫 Telegram 메시지부터 `공지 열기`, `국문 공지`, `영문 공지` 버튼을 모두 포함합니다.
- 정상 발송된 공지 ID는 `data/state.json`에 저장해 중복 알림을 막습니다.
- 한 번에 공지가 많이 추가되면 최대 10개씩 보내고 다음 실행에서 이어서 처리합니다.
- 새 공지가 없어도 30일마다 heartbeat를 커밋해 공개 저장소의 예약 워크플로가 비활성화되는 것을 방지합니다.

## 선택: 국문·영문 공지 확장 연결

[KISA-Bohonara-Support](https://github.com/doraemyon/KISA-Bohonara-Support)를 연결하면 국문·영문 문서 링크가 최초 알림에 바로 표시됩니다. 문서는 별도 Action에서 생성되므로 알림 직후 링크를 누르면 잠시 페이지가 준비되지 않았을 수 있지만, 보통 1분 안팎 후 같은 링크에서 열립니다.

기본 GitHub Pages 주소는 `NOTICE_EXTENSION_REPOSITORY` 값으로 자동 계산합니다. 사용자 도메인 등 다른 Pages 주소를 쓰는 경우에만 저장소 **Settings → Secrets and variables → Actions → Variables**에 다음 값을 추가합니다.

| Variable 이름 | 값 |
| --- | --- |
| `NOTICE_EXTENSION_PUBLIC_BASE_URL` | 예: `https://example.com/kisa-notices` |

Telegram 메시지를 나중에 수정하지 않으므로 Support 저장소에는 Telegram bot token이나 chat ID를 등록할 필요가 없습니다. 수신 대상을 개인 대화에서 채널로 바꿔도 이 저장소의 `TELEGRAM_CHAT_ID` 하나만 변경하면 됩니다.

## 문제 해결

- `Required secret TELEGRAM_BOT_TOKEN is empty or missing`: GitHub Secret 이름과 값을 확인하세요.
- `Required secret TELEGRAM_CHAT_ID is empty or missing`: `/start`를 보낸 후 chat ID 확인 스크립트를 다시 실행하세요.
- `Unauthorized`: BotFather token이 잘못됐거나 폐기됐습니다. 새 token을 발급해 Secret을 교체하세요.
- `Bad Request: chat not found`: `TELEGRAM_CHAT_ID`가 잘못됐거나 봇과 대화를 시작하지 않았습니다.
- 국문·영문 버튼이 보이지 않음: `NOTICE_EXTENSION_REPOSITORY`와 `NOTICE_EXTENSION_TOKEN`이 모두 등록돼 있는지 확인하세요.
- 국문·영문 버튼을 바로 눌렀을 때 404가 표시됨: 문서 생성 Action이 아직 진행 중일 수 있습니다. Support 저장소의 Action 상태를 확인하고 잠시 후 다시 여세요.
- cron-job.org가 `401` 또는 `404`를 반환: GitHub PAT의 저장소 지정, `Actions: Read and write` 권한, 만료일을 확인하세요.
- Actions 자체 cron이 드문드문 실행됨: 예상되는 GitHub 스케줄러 특성입니다. cron-job.org의 실행 기록과 `workflow_dispatch` 실행 여부를 확인하세요.
