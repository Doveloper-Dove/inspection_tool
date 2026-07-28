다중 AI 뉴스 댓글 담론 분류기 GUI — Vertex AI 추가 / 로컬 호출 제한 해제
=============================================================================

추가된 AI
---------
- Google Vertex AI (Express Mode)
- GUI에 Google Cloud Express Mode API 키 하나를 입력하는 방식입니다.
- 기본 모델: gemini-3.1-flash-lite
- 표준 서비스 계정 JSON 방식이 아니라 배포가 간단한 Express Mode를 사용합니다.

호출 제한 변경
--------------
- 기존 프로그램 실행당 최대 5회 제한을 완전히 제거했습니다.
- 기사당 댓글 개수 제한도 없습니다.
- 미분석 기사 묶음이 남아 있으면 사용자가 중지하거나 오류가 발생할 때까지 계속 처리합니다.
- 실제 요청 누적 횟수는 GUI에 표시됩니다.

여전히 적용되는 제한
--------------------
- Gemini, Vertex AI, OpenAI, Groq, OpenRouter, Ollama 서비스 자체의 요금/할당량/분당 요청/토큰 제한
- 요청 실패 시 무한 반복을 방지하는 최대 재시도 횟수
- 사용자가 누르는 중지 버튼

실행
----
python -m pip install -r requirements_multi_ai_comment_gui_vertex.txt
python multi_ai_comment_discourse_gui_vertex_unlimited.py

EXE 제작
--------
다음 파일을 같은 폴더에 둡니다.
- multi_ai_comment_discourse_gui_vertex_unlimited.py
- requirements_multi_ai_comment_gui_vertex.txt
- build_gui_exe_vertex_unlimited.bat

build_gui_exe_vertex_unlimited.bat를 실행하면 다음 파일이 생성됩니다.
dist\AI댓글담론분류기.exe

보안
----
API 키는 GUI 입력칸에서 실행 중 메모리로만 사용하며 파일이나 결과 CSV에 저장하지 않습니다.
유료 API를 사용할 때는 프로그램 내부 상한이 없으므로 비용과 서비스별 사용량을 직접 확인하십시오.
