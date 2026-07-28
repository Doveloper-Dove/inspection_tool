# word2vec_korean.py 사용법

한국어 문장을 형태소 분석 후 Word2Vec으로 학습해서 단어 간 유사도를 확인하는 도구입니다.

품사 태그(NNG, NNP...)나 사용자 단어의 `score`, Word2Vec/PCA의 동작 원리가 궁금하다면 [CONCEPTS.md](CONCEPTS.md)를 참고하세요.

## 준비

이 프로젝트의 기본 `.venv`는 Python 3.14라서 gensim이 빌드되지 않습니다.
그래서 이 도구 전용으로 `inspection-tool/.venv312` (Python 3.12) 가상환경을 따로 만들어 두었습니다.
아래 명령은 모두 이 가상환경의 python을 사용합니다.

가상환경이 없다면 새로 만들기:

```powershell
py -3.12 -m venv inspection-tool\.venv312
inspection-tool\.venv312\Scripts\python.exe -m pip install -r inspection-tool\requirements.txt
```

## 실행

Windows 콘솔은 기본 인코딩 문제로 한글이 깨져 보일 수 있으니 `PYTHONUTF8=1`을 함께 설정합니다.

```powershell
$env:PYTHONUTF8=1
inspection-tool\.venv312\Scripts\python.exe inspection-tool\word2vec_korean.py --words 학교 친구
```

입력 파일 없이 실행하면 스크립트에 내장된 샘플 문장 10개로 학습합니다.

## 옵션

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `--input`, `-i` | 문장이 한 줄에 하나씩 담긴 텍스트 파일 경로 (미지정 시 샘플 문장 사용) | 없음 |
| `--words`, `-w` | 유사 단어를 확인할 단어 목록 (여러 개 가능) | 어휘 상위 5개 |
| `--top-n` | 단어별로 출력할 유사 단어 개수 | 10 |
| `--vector-size` | 임베딩 벡터 차원 수 | 100 |
| `--window` | 문맥 윈도우 크기 | 5 |
| `--min-count` | 학습에 포함할 단어의 최소 등장 횟수 | 1 |
| `--epochs` | 학습 반복 횟수 | 50 |
| `--sg` | 학습 방식: `1`=Skip-gram, `0`=CBOW | 1 |
| `--save` | 학습된 모델을 저장할 경로 | 없음 |
| `--plot` | 임베딩을 PCA로 2D 축소해서 시각화한 이미지를 저장할 경로 (예: `plot.png`) | 없음 |
| `--plot-max-words` | `--words` 미지정 시 시각화할 최대 단어 수 | 50 |
| `--user-words` | 형태소 분석기에 등록할 신조어 목록. `단어`, `단어:태그`, `단어:태그:점수` 형식 (여러 개 가능, 기본 태그 `NNP`) | 없음 |
| `--user-dict` | 형태소 분석기에 등록할 사용자 사전 파일 경로 (탭 구분: `단어\t태그\t점수`) | 없음 |

## 사용 예시

내 문장 파일로 분석하기 (`sentences.txt`에 문장을 한 줄씩 작성):

```powershell
$env:PYTHONUTF8=1
inspection-tool\.venv312\Scripts\python.exe inspection-tool\word2vec_korean.py `
  --input sentences.txt `
  --words 영화 배우 `
  --top-n 5 `
  --vector-size 50 `
  --epochs 100
```

모델을 저장해 두고 나중에 재사용하기:

```powershell
inspection-tool\.venv312\Scripts\python.exe inspection-tool\word2vec_korean.py --input sentences.txt --save model.bin
```

저장된 모델은 gensim으로 다시 불러올 수 있습니다.

```python
from gensim.models import Word2Vec
model = Word2Vec.load("model.bin")
model.wv.most_similar("친구")
```

임베딩을 2D 이미지로 시각화하기 (PCA로 차원 축소 후 산점도 저장):

```powershell
$env:PYTHONUTF8=1
inspection-tool\.venv312\Scripts\python.exe inspection-tool\word2vec_korean.py --input sentences.txt --plot plot.png
```

특정 단어들만 시각화하려면 `--words`와 함께 사용합니다.

```powershell
inspection-tool\.venv312\Scripts\python.exe inspection-tool\word2vec_korean.py --input sentences.txt --words 학교 친구 영화 회사 --plot plot.png
```

## 참고

- 형태소 분석에는 `kiwipiepy`를 사용하며, 설치되어 있지 않으면 공백 기준 토큰화로 자동 대체됩니다.
- 문장 수가 적으면(특히 샘플 10개) 유사도 점수의 의미가 크지 않습니다. 실제 분석에는 충분한 양의 문장 데이터를 `--input`으로 넣어주세요.
- `--plot`은 100차원 이상의 임베딩을 PCA로 2차원으로 압축해서 보여주므로 원래 공간의 거리 관계가 일부 왜곡될 수 있습니다.
- 시스템에 한글 폰트(Malgun Gothic 등)가 없으면 그래프의 단어 라벨이 깨질 수 있습니다.
