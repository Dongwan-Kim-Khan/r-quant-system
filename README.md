# 🏛️ 알상무 퀀트 나스닥 모닝 브리핑 & 포워드 트래커 봇

> **기능**: 매일 아침 8:30(KST) 나스닥 30대 핵심 유니버스를 스캔하여 **추천 2선 / 애매 2선 / 반대 2선 (총 6종목)**을 선정하고, 과거 추천 종목들의 실시간 수익률을 자동 추적(Forward Tracking)하여 모델 유효성을 자가 검증한 뒤 **이메일로 일일 리포트를 자동 발송**합니다.

---

## 🚀 깃허브 액션 (GitHub Actions) 연동 가이드

### 1. 깃허브 저장소(Repository)에 파일 푸시
이 `al_sangmoo_project` 폴더를 본인의 깃허브 저장소에 Push합니다.

### 2. 이메일 발송용 GitHub Secrets 3개 등록
GitHub 저장소 ➔ **Settings** ➔ **Secrets and variables** ➔ **Actions** ➔ **New repository secret**에 다음 3개를 등록합니다:

| Secret 이름 | 설명 | 예시 값 |
| :--- | :--- | :--- |
| `EMAIL_SENDER` | 발송할 구글 계정 이메일 | `myemail@gmail.com` |
| `EMAIL_PASSWORD` | 구글 2단계 인증용 **앱 비밀번호(16자리)** | `abcd efgh ijkl mnop` |
| `EMAIL_RECEIVER` | 리포트를 수신받을 이메일 주소 | `myemail@naver.com` 또는 `myemail@gmail.com` |

> 💡 **구글 앱 비밀번호 발급 방법 (1분 소요)**:
> 1. [구글 계정 보안 설정](https://myaccount.google.com/security) 접속
> 2. **2단계 인증** 활성화
> 3. 검색창에 **"앱 비밀번호(App Passwords)"** 검색 ➔ 앱 이름에 `QuantBot` 입력 후 생성된 16자리 비밀번호 복사

---

## ⏰ 실행 스케줄
* **실행 시점**: 매주 **월요일~금요일 한국 시간 오전 08:30 (KST)** 자동 실행
* **수동 즉시 실행**: 깃허브 저장소의 **Actions** 탭 ➔ `Daily Al-Sangmoo Quant Briefing` ➔ **Run workflow** 클릭 시 언제든 즉시 실행 가능
