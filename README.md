# 구카와

`chiikawa.anime`의 영상을 수집하고, 화면의 일본어·영어 자막을 읽어 한국어 자막으로 번역해 보는 개인 보관용 프로젝트다.
워커가 새 회차를 주기적으로 받아 원문 추출 → 번역까지 자동으로 처리하고, 웹 뷰어에서 한국어 자막과 함께 차례로 볼 수 있다.

- 번역 원문은 **화면의 일본어 자막(OCR)** 이다. 영어 자막은 OCR 오류 보정용 보조 입력으로만 쓴다.
- 기본 번역 모델은 `gpt-6.1-terra`(OpenAI Responses API)이며 `.env`의 `TRANSLATION_MODEL`로 바꾼다.
- 영상·자막·DB는 저장소에 포함하지 않는다. 모두 `data/`에 저장되며 `.gitignore` 대상이다.

## 현재 동작

- 최초 실행 시 일반 게시물과 릴스 목록에서 **360화 이상**인 영상만 작업 목록에 등록한다. 359화 이하와 회차 번호를 확인할 수 없는 게시물은 다운로드하지 않는다.
- 이후 기본 1시간 간격으로 최신 게시물을 확인한다. 고정 게시물을 고려하며, 알려진 게시물에 도달할 때까지 확인해 장기간 중단 후의 신규 게시물도 찾는다.
- 게시물 ID로 중복을 막는다. 완료된 영상은 다시 다운로드하지 않는다.
- 원본 파일을 재인코딩 없이 보관한다. 별도 영어 자막이 제공되면 이를 사용하고, 없으면 화면의 영어 문장을 OCR로 읽는다.
- 다운로드는 `bv*+ba/b` 선택자로 고해상도 영상 전용 스트림과 별도 음성도 확인한다. 분리 스트림은 FFmpeg로 재인코딩 없이 합친다. 선택한 형식은 `download-formats.json`에 기록한다. 기존에 저장한 파일을 다시 받는 작업은 별도로 실행해야 한다.
- 원문 텍스트, 타임코드 JSON, SRT, WebVTT, 게시물 설명, 체크섬, OCR 인식 기록을 저장한다.
- 실패한 작업은 기본 3회까지 재시도한다. 중단된 작업과 누락된 저장 파일은 다시 처리한다.
- 전체 스캔이 실패하면 완료로 표시하지 않는다. 다음 주기에 다시 확인하며, 앞서 등록한 게시물은 중복되지 않는다.

게시물 변경 감지는 주기적인 조회 방식이다. 인스타그램에 남아 있고 현재 계정으로 접근 가능한 360화 이상의 단일 영상이 수집 대상이다. `.env`의 `MIN_EPISODE=360`으로 시작 회차를 지정한다. 최신 감지, 과거 회차 수집, 주소로 등록한 작업에 모두 적용하며, 이미 대기 중인 범위 밖 작업도 다운로드 전에 `skipped`로 표시해 제외한다. 삭제된 회차를 복원하거나 계정에 없는 회차를 만들어 내지는 않는다. 여러 영상이 묶인 캐러셀은 제외한다. 로컬 파일 가져오기는 다운로드를 수행하지 않으므로 이 제한을 적용하지 않는다.

## 서버 배포 (Docker)

### 1. 코드와 설정

```bash
git clone https://github.com/lgun/gukawa.git
cd gukawa
cp .env.example .env
mkdir -p data secrets
```

`.env`에서 최소한 다음을 채운다. `.env`는 커밋하지 않는다.

- `ADMIN_PASSWORD`: 감상 화면과 관리 화면의 로그인 비밀번호 (사용자 이름 `admin`)
- `OPENAI_API_KEY`: 번역용 키
- `PROCESSING_MODE=translate`, `AUTO_SYNC=true`, `TRANSLATION_MODEL=gpt-6.1-terra` (예시 기본값)
- `GUKAWA_BIND`: 기본 `127.0.0.1`. 리버스 프록시(Nginx, Caddy 등) 없이 외부에서 바로 접속하려면 `0.0.0.0`

### 2. 기존 데이터 올리기 (선택)

로컬에서 받은 데이터를 쓰려면 **컨테이너를 띄우기 전에** 프로젝트 폴더의 `data/` 아래에 그대로 올린다.
Compose가 `./data`를 컨테이너의 `/data`에 연결한다.

```
gukawa/                  ← git clone 한 폴더
└─ data/
   ├─ gukawa.sqlite3     ← 회차 목록·상태 DB (SQLite, 별도 DB 서버 불필요)
   ├─ episodes/          ← 회차별 영상·원문·자막·번역 (게시물 ID 폴더)
   └─ models/            ← OCR 모델 (없으면 첫 실행 때 자동으로 받음)
```

컨테이너는 UID 10001 사용자로 실행되므로 올린 뒤 소유자를 맞춘다.

```bash
sudo chown -R 10001:10001 data
```

`data/`가 비어 있으면 처음부터 `MIN_EPISODE` 이후 회차를 수집하고 번역한다.

### 3. 실행

```bash
docker compose up --build -d
docker compose logs -f worker
```

감상 화면: `http://<서버>:8000` (또는 리버스 프록시 주소) · 관리 화면: `/admin`.

- `/`: 회차 목록, `/episodes/<ID>`: 플레이어 (이전화·다음화, 회차 바로가기, 재생 목록, 전체 화면, 끝나면 다음화 자동 재생)
- 단축키: Space 재생·일시정지, ←/→ 5초 이동, F 전체 화면, M 음소거, P/N 이전화·다음화
- 모든 화면과 파일은 로그인이 필요하다. 처음 접속하면 `/login` 로그인 페이지가 열리고, 로그인하면 30일 동안 유지되는 쿠키 세션이 만들어진다(브라우저 기본 로그인 팝업을 쓰지 않아 모든 브라우저에서 동작). `admin` / `ADMIN_PASSWORD`는 관리자, `.env`의 `VIEWER_USERNAME` / `VIEWER_PASSWORD`는 감상 전용 계정(목록·플레이어만, `/admin` 불가)이다. 세션 서명 키는 `data/session.key`에 자동 생성되며, 비밀번호를 바꾸면 그 계정의 기존 로그인은 풀린다. 같은 IP에서 10분 동안 10번 틀리면 잠시 막힌다. `LOCAL_PREVIEW=true`는 같은 PC(127.0.0.1)에서만 로그인 없이 보는 개발용 설정이며 Docker에서는 적용되지 않는다.

데이터는 `./data`에 남으므로 `docker compose down` 후에도 유지된다. 백업은 `data/` 폴더를 복사하면 된다(SQLite 파일 포함, 복사 전 `docker compose stop` 권장).

```powershell
# 360화 이상인 과거 회차를 다시 확인하고 누락 파일을 복구한다.
docker compose exec web python -m gukawa.cli scan --backfill

# 저장 현황 및 파일 무결성 검사
docker compose exec web python -m gukawa.cli status
docker compose exec web python -m gukawa.cli verify --checksum

# 실패 작업 재시도: 실행 중인 워커가 파일을 쓰지 않도록 먼저 중지한다.
docker compose stop worker
docker compose run --rm worker python -m gukawa.cli retry --failed
docker compose up -d worker
```

전체 스캔은 먼저 작업 목록을 채운 뒤 영상을 차례로 처리한다. 많은 게시물이나 요청 제한으로 스캔이 오래 걸릴 수 있다. 인스타그램의 페이지·인증 방식이 바뀌면 수집 도구 업데이트가 필요할 수 있다.

## 인스타그램 로그인 세션

공개 접근을 먼저 시도한다. 로그인이 필요하거나 요청이 제한되면 브라우저에서 직접 로그인한 후 쿠키 파일을 준비한다. 비밀번호를 코드에 넣지 않는다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[ocr]'

# Firefox에 로그인한 상태에서 사용자가 직접 실행한다.
# 인스타그램 도메인의 쿠키만 secrets/instagram-cookies.txt로 저장한다.
.\.venv\Scripts\python.exe -m gukawa.cli cookies --browser firefox
```

Netscape 형식의 쿠키를 직접 `secrets/instagram-cookies.txt`에 두어도 된다. 쿠키와 `.env`는 Git 추적에서 제외되어 있다. 브라우저 암호화 방식에 따라 Chrome/Edge에서 읽기가 실패할 수 있다. 컨테이너에는 쿠키를 읽기 전용으로 연결하고, 수집기는 임시 복사본을 사용한다.

## 한 편씩 검증하기

전체 자동 수집 전에 `.env`의 `AUTO_SYNC=false`로 설정할 수 있다. 설정 변경 후에는 컨테이너를 다시 생성한다.

```powershell
docker compose up -d
docker compose exec web python -m gukawa.cli add https://www.instagram.com/p/게시물ID/
```

워커가 영상 한 편을 처리하면 관리자 화면에서 원본과 영어 자막을 함께 열어 확인한다.
실제 인스타그램 영상 대신 로컬 MP4로도 검사할 수 있다.

```powershell
# 로컬 실행에는 ffprobe/FFmpeg가 PATH에 있어야 한다.
# --subtitles를 생략하면 OCR로 영어 원문을 추출한다.
.\.venv\Scripts\python.exe -m gukawa.cli import --video C:\videos\sample.mp4 --title '저장 검증' --subtitles C:\videos\sample.en.srt
.\.venv\Scripts\python.exe -m gukawa.cli process --all
.\.venv\Scripts\python.exe -m gukawa.cli verify --checksum
```

로컬 실행과 Docker의 저장 위치는 다르다. 로컬은 `./data`, Compose는 `/data`에 연결한 볼륨을 사용한다. 로컬에서 웹과 워커를 함께 실행하려면 다음 두 명령을 각각 실행한다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn gukawa.web:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m gukawa.worker
```

회차별 저장 구조:

```text
data/episodes/<게시물ID>/
  original.mp4              다운로드한 원본 영상
  metadata.json             게시물 설명, 회차 번호, 원본 체크섬, 추출 방식
  source.txt                영어 원문
  source.json               start/end/text를 담은 원문 자막
  en.srt                    타임코드가 있는 영어 자막
  en.vtt                    브라우저에서 확인하는 영어 자막
  ocr-observations.jsonl     OCR로 읽은 시각·문장·인식 점수
```

원문은 **화면에 표시된 영어 자막을 읽은 결과**다. 일본어 음성을 전사한 결과와 구분한다. 원본 영어 자막 자체의 번역 오류, OCR 누락이나 오인식은 남을 수 있다. OCR 결과는 `manually_verified=false`로 기록하며, 문장이 하나도 없거나 자막 시간이 잘못되면 성공으로 처리하지 않는다. `verify`는 파일과 시간 정보의 무결성을 확인하며 영어 문장의 정확도를 보장하지 않는다. 저장 완료 회차가 없으면 종료 코드 2를 반환한다.

`OCR_CROP_TOP`/`OCR_CROP_BOTTOM`은 전체 화면 높이에 대한 자막 탐색 영역이고, 기본값은 0.55~1.0이다. 기본 4fps로 읽고 연속 두 프레임 이상 등장한 문장을 자막으로 묶는다. 0.5초보다 짧은 자막이 빠질 수 있고, 샘플 간격만큼 시간 오차가 생길 수 있다. 실제 영상 레이아웃과 빠른 대사를 샘플로 확인한 뒤 값을 조정한다. 첫 OCR 실행은 모델을 내려받으므로 인터넷 연결이 필요하다.

영역을 수정하고 원문을 다시 추출하려면 워커를 중지한 상태에서 다음 명령을 사용한다. 원본 영상은 보관하고, 해당 회차의 원문·번역 캐시를 새로 만든다.

```powershell
docker compose stop worker
docker compose run --rm worker python -m gukawa.cli retry 게시물ID --reextract
docker compose up -d worker
```

## 본편만 남긴 재생용 영상

확인한 인스타그램 영상은 세로 화면에 위아래 반복 화면과 가운데 본편이 합쳐져 있다. 720×1280 영상은 `x=0, y=452, width=720, height=388`, 1080×1920 영상은 이에 비례하는 `x=0, y=678, width=1080, height=582` 영역을 잘라 만든다. 경계에 겹친 표기를 피하려고 본편 위쪽 가장자리도 일부 잘라내는, 현재 영상 구성에 맞춘 프리셋이다. 원본 영상은 수정하지 않는다.

- `original.mp4`: 다운로드한 인스타그램 원본
- `playback.mp4`: 위아래 반복 화면을 잘라낸 재생용 영상. 입력에 따라 720×388 또는 1080×582이며 업스케일하지 않는다.
- `playback.jpg`: 재생 화면 미리보기
- `playback.json`: 자르기 영역, 원본 체크섬, 출력 크기와 재생 시간

웹에서는 재생용 파일이 있으면 본편을 먼저 재생하며, **인스타 원본 보기**로 원본도 확인할 수 있다. 기존 영어 자막은 화면에 새겨져 있어 재생용 영상에도 남는다. 별도 자막의 타임코드는 변하지 않는다. 영상은 H.264로 다시 인코딩하고, 음성 패킷은 복사한다. 원본에 없던 세부 화질을 복원하거나 임의로 해상도를 늘리지는 않는다.

새 수집 작업은 `CREATE_PLAYBACK=true`일 때 재생용 파일도 만든다. 확인하지 않은 화면 크기이면 자동 자르기를 건너뛰고 원본을 보관한다. 같은 화면 크기라도 구성이 달라지면 자르기 영역을 다시 확인해야 한다.

기존 회차 전체에 재생용 파일을 만들려면 워커를 중지한 상태에서 실행한다. 완성된 재생용 파일은 원본 체크섬과 프리셋이 같으면 재사용한다.

```powershell
docker compose stop worker
docker compose run --rm worker python -m gukawa.cli prepare-playback --all
docker compose up -d worker
```

Compose는 프로젝트의 `data/`를 컨테이너의 `/data`에 연결하므로 로컬 명령과 컨테이너가 같은 파일을 쓴다.

## 별도 한국어 자막과 배경 표시

한국어는 영상에 영구적으로 합치지 않고 `ko.vtt`와 `ko.srt`로 저장한다. 웹 플레이어는 `ko.vtt`가 준비된 회차에서 한국어를 기본으로 표시한다. 자막이 없으면 기존 영상 플레이어로 재생하고 준비되지 않은 한국어 문장을 만들어 보여주지 않는다.

- 한국어 글자 바로 뒤에만 어두운 배경을 적용한다. 여러 줄이거나 자동 줄바꿈이 생기면 각 줄의 글자 길이에 맞춘다.
- 자막이 없는 시간에는 글자와 배경을 모두 숨긴다. 한국어 자막은 켜고 끌 수 있다.
- 실제 영상 영역을 기준으로 자막을 배치한다. 인스타 원본 보기에서는 가운데 패널, 본편 보기에서는 아래쪽에 표시한다.
- 자막 위치는 OCR 탐색 범위와 구분한다. 원본의 기본 자막 하단은 높이의 0.66이고, 회차별 `metadata.json`의 `subtitle_region`으로 조정할 수 있다.
- 전체 화면은 영상과 자막을 함께 전환한다. 해당 브라우저가 요소 전체 화면을 지원하지 않으면 페이지 화면을 채우는 방식으로 표시한다.

내보낸 한국어 VTT에도 글자 배경색을 넣는다. SRT 파일에는 문장과 시간만 저장하고, 웹의 글자 배경은 플레이어에서 처리한다. 외부 플레이어의 스타일 지원은 다를 수 있다.

한국어 파일 수정은 영상 재인코딩 없이 반영할 수 있다.

## 번역

`PROCESSING_MODE=translate`에서 워커는 새 회차마다 다음을 수행한다.

1. 영어 자막 OCR(PP-OCRv4)로 자막 구간과 시간을 정한다.
2. 같은 구간의 **일본어 자막**을 PP-OCRv6 다국어 모델로 읽어 `source.ja.json`에 저장한다. 중국어 줄은 버린다.
3. 일본어(원문) + 영어(보조) + 직전 2화의 번역을 문맥으로 OpenAI Responses API에 보낸다. 모델은 줄마다 화자를 먼저 판단하고 그 말투로 번역한다.
4. 결과를 `ko.api.*`로 저장하고 기본 자막 `ko.*`으로 지정한다. 요청·응답은 `translation.api.audit.json`에 남는다(키는 기록하지 않음).

프롬프트는 `gukawa/translation.py`의 `TRANSLATION_PROMPT`이며 바꿀 때마다 `PROMPT_VERSION`을 올린다. 특정 회차의 대사를 예시로 넣으면 그 회차로는 공정한 비교가 되지 않으므로, 예시는 지어낸 문장이나 비교에 쓰지 않는 회차에서 고른다.

```bash
# 저장된 회차의 일본어 자막 추출 (API 미사용)
docker compose exec worker python -m gukawa.cli extract-ja --all

# 한 회차를 다시 번역해 기본 자막으로 지정 (유료 요청)
docker compose exec worker python -m gukawa.cli translate-api <ID> --model gpt-6.1-terra --force --make-default
```

`translate-api`는 기본적으로 결과를 비교용 `api` 버전으로만 저장한다. `--make-default`를 주면 기본 자막과 한국어 제목도 바꾼다. 이전 직접 번역은 `ko.direct.*`로 남아 플레이어의 번역 선택에서 고를 수 있다. `--no-japanese`, `--no-context`로 일본어 원문이나 이전 화 문맥 없이 비교할 수 있다.

## 테스트

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# FFmpeg와 실제 OCR을 포함한 검사. 모델 다운로드가 발생한다.
docker run --rm -v "$PWD:/app:ro" -e RUN_OCR_INTEGRATION=1 gukawa:local python -m unittest discover -s tests -v
```

수집 구현 참고: [Instaloader 게시물 조회](https://instaloader.github.io/module/structures.html), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [RapidOCR](https://github.com/RapidAI/RapidOCR), [OpenAI 구조화된 출력](https://developers.openai.com/api/docs/guides/structured-outputs).
