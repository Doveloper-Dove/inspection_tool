import argparse
import sys
from pathlib import Path

import numpy as np
from gensim.models import Word2Vec

SAMPLE_SENTENCES = [
    "나는 오늘 학교에 가서 친구를 만났다",
    "그는 매일 아침 커피를 마시며 신문을 읽는다",
    "고양이가 소파 위에서 잠을 잔다",
    "강아지가 공원에서 신나게 뛰어논다",
    "그녀는 새로운 책을 읽으며 하루를 보냈다",
    "우리는 주말에 영화를 보러 극장에 갔다",
    "아이들이 놀이터에서 즐겁게 놀고 있다",
    "선생님이 학생들에게 숙제를 내주었다",
    "비가 오는 날에는 우산을 챙긴다",
    "그는 회사에서 열심히 일을 한다",
]

# 세종 품사 태그 중 "내용어"만 남긴다. NNG/NNP=명사, VV=동사, VA=형용사, MAG=부사.
# 조사(JKS 등)·어미(EF, EC 등)는 문법 기능만 하고 의미가 없어서 제외 — 안 그러면
# "학교는"/"학교에서"/"학교를"이 전부 다른 단어로 학습돼 문맥 데이터가 흩어진다.
DEFAULT_POS = {"NNG", "NNP", "VV", "VA", "MAG"}


# --user-words로 들어온 문자열 하나를 (단어, 태그, 점수)로 쪼개는 헬퍼.
# build_tokenizer 안에서 각 --user-words 항목마다 호출된다.
def parse_user_word(spec: str) -> tuple[str, str, float]:
    # "단어" / "단어:태그" / "단어:태그:점수" 형식을 파싱. 태그 기본값은 고유명사(NNP).
    parts = spec.split(":")
    word = parts[0]
    tag = parts[1] if len(parts) > 1 and parts[1] else "NNP"
    score = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
    return word, tag, score


# 문장을 토큰 리스트로 바꿔주는 tokenize 함수를 "만들어서" 반환하는 팩토리 함수.
# main()에서 딱 한 번 호출되고, 여기서 반환된 tokenize 함수가 이후 tokenize_sentences에
# 전달되어 모든 문장에 반복 적용된다. kiwipiepy가 없으면 None을 반환해서
# main()이 simple_tokenize(공백 분리)로 대체하게 만든다.
def build_tokenizer(user_words=None, user_dict_path=None):
    try:
        from kiwipiepy import Kiwi
    except ImportError:
        return None

    kiwi = Kiwi()

    for spec in user_words or []:
        word, tag, score = parse_user_word(spec)
        # score는 같은 표기를 여러 갈래로 쪼갤 수 있을 때(예: "갓생살기" -> "갓+생+살+기" vs
        # "갓생+살+기") 어느 쪽을 우선할지 정하는 가중치다. 기본 0.0이면 통계 모델이 알아서
        # 더 자연스러운 쪽을 고르고, 값을 올리면 이 신조어 쪽을 강제로 우선시한다.
        if not kiwi.add_user_word(word, tag, score):
            print(f"[경고] '{word}'({tag}) 사용자 단어 등록에 실패했습니다.")

    if user_dict_path:
        added = kiwi.load_user_dictionary(user_dict_path)
        print(f"사용자 사전 로드 완료: {user_dict_path} ({added}개 단어)")

    def tokenize(sentence: str):
        # kiwi.tokenize()는 형태소마다 (형태, 품사 태그) 쌍을 내놓는다.
        # DEFAULT_POS에 없는 태그(조사·어미 등)는 여기서 걸러진다.
        return [token.form for token in kiwi.tokenize(sentence) if token.tag in DEFAULT_POS]

    return tokenize


# kiwipiepy 미설치 시 build_tokenizer 대신 쓰이는 대체용 토크나이저.
# 형태소 분석 없이 공백으로만 문장을 자른다 (품질은 떨어지지만 의존성 없이 동작 보장용).
def simple_tokenize(sentence: str):
    return sentence.split()


# 학습에 쓸 원본 문장들을 준비하는 함수. main()이 가장 먼저 호출한다.
# --input 경로가 있으면 그 파일을, 없으면 SAMPLE_SENTENCES를 사용한다.
def load_sentences(path: str | None) -> list[str]:
    if path is None:
        return SAMPLE_SENTENCES
    text = Path(path).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


# load_sentences가 만든 문장 리스트 전체에 build_tokenizer(또는 simple_tokenize)를
# 적용해서, gensim이 요구하는 "토큰 리스트의 리스트" 형태로 바꿔주는 중간 단계.
# main()에서 train_word2vec 바로 직전에 호출된다.
def tokenize_sentences(sentences, tokenizer):
    return [tokenizer(sentence) for sentence in sentences]


# 토큰화된 문장들로 실제 gensim Word2Vec 모델을 학습시키는 함수.
# main()에서 tokenize_sentences 결과를 받아 호출하고, 반환된 모델이
# report/save/plot_embeddings에 그대로 전달되어 이후 모든 작업의 기반이 된다.
def train_word2vec(tokenized_sentences, vector_size, window, min_count, epochs, sg):
    # sg=1(Skip-gram): 가운데 단어로 주변 단어를 맞히며 학습 -> 희귀 단어에도 비교적 강함.
    # sg=0(CBOW): 주변 단어들로 가운데 단어를 맞히며 학습 -> 더 빠르지만 흔한 단어에 유리.
    # "정답 맞히기"가 목적이 아니라, 맞히려 애쓰는 과정에서 비슷한 문맥의 단어끼리
    # 벡터가 가까워지는 부산물(단어 임베딩)을 얻는 것이 핵심이다.
    return Word2Vec(
        sentences=tokenized_sentences,
        vector_size=vector_size,  # 단어 하나를 표현하는 벡터의 차원 수
        window=window,  # 앞뒤 몇 단어까지를 "같은 문맥"으로 볼지
        min_count=min_count,  # 이보다 적게 등장한 단어는 노이즈로 보고 학습에서 제외
        epochs=epochs,  # 전체 문장을 몇 번 반복 학습할지
        sg=sg,
        workers=4,
    )


# 학습 결과를 사람이 읽을 수 있게 콘솔에 출력하는 함수. main()에서 학습 직후 호출된다.
# --words로 단어를 지정하지 않으면 어휘 중 상위 5개를 대신 보여준다.
def report(model: Word2Vec, query_words, top_n):
    vocab = model.wv.index_to_key
    print(f"어휘 수: {len(vocab)}")

    targets = query_words if query_words else vocab[:5]
    for word in targets:
        if word not in model.wv:
            print(f"[건너뜀] '{word}'는 어휘에 없습니다 (min_count 설정을 확인하세요)")
            continue
        print(f"\n'{word}'와 유사한 단어 (top {top_n})")
        # most_similar는 두 단어 벡터의 코사인 유사도(방향이 얼마나 비슷한지, -1~1)로
        # 순위를 매긴다. 1에 가까울수록 비슷한 문맥에서 쓰였다는 뜻.
        for similar_word, score in model.wv.most_similar(word, topn=top_n):
            print(f"  {similar_word}\t{score:.4f}")


# 여러 단어의 고차원 벡터를 2D 좌표로 압축하는 순수 계산 함수.
# plot_embeddings 안에서만 호출되는 내부 헬퍼다.
def pca_2d(vectors: np.ndarray) -> np.ndarray:
    # PCA(주성분 분석): 고차원 벡터를 "정보 손실이 가장 적은 2개의 축"에 투영해 2D로 압축.
    # 1) 평균을 빼서 원점 중심으로 이동
    centered = vectors - vectors.mean(axis=0)
    # 2) SVD로 분산이 큰 순서대로 방향(축)들을 구한다 (vt의 각 행이 축 하나)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    # 3) 분산이 가장 큰 두 축에 투영 -> 2D 좌표. 즉 그래프 거리는 근사치일 뿐,
    #    정확한 유사도 비교는 원래 차원에서 계산한 most_similar를 봐야 한다.
    return centered @ vt[:2].T


# matplotlib이 한글을 네모(□)로 깨뜨리지 않도록 폰트를 설정하는 헬퍼.
# plot_embeddings가 그래프를 그리기 직전에 한 번 호출한다.
def setup_korean_font():
    import matplotlib
    from matplotlib import font_manager

    korean_fonts = {"Malgun Gothic", "AppleGothic", "NanumGothic"}
    available = {f.name for f in font_manager.fontManager.ttflist}
    for font in korean_fonts:
        if font in available:
            matplotlib.rcParams["font.family"] = font
            break
    else:
        print("[경고] 한글 폰트를 찾지 못해 그래프의 단어 라벨이 깨질 수 있습니다.")
    matplotlib.rcParams["axes.unicode_minus"] = False


# --plot 옵션이 주어졌을 때 main()이 맨 마지막에 호출하는 함수.
# 학습된 모델에서 단어 벡터를 뽑아 pca_2d로 축소하고, 이미지 파일로 저장까지 담당한다.
def plot_embeddings(model: Word2Vec, path: str, words, max_words: int):
    import matplotlib.pyplot as plt

    setup_korean_font()

    vocab = [w for w in words if w in model.wv] if words else model.wv.index_to_key[:max_words]
    if len(vocab) < 2:
        print("[경고] 시각화할 단어가 2개 미만이라 그래프를 생략합니다.")
        return

    vectors = np.array([model.wv[w] for w in vocab])
    reduced = pca_2d(vectors)

    plt.figure(figsize=(10, 8))
    plt.scatter(reduced[:, 0], reduced[:, 1], alpha=0.6)
    for word, (x, y) in zip(vocab, reduced):
        plt.annotate(word, (x, y), fontsize=9, xytext=(3, 3), textcoords="offset points")
    plt.title("Word2Vec 임베딩 시각화 (PCA, 2D)")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"\n시각화 저장 완료: {path}")


# 스크립트의 진입점이자 전체 파이프라인을 순서대로 이어붙이는 함수.
# CLI 인자를 읽고, load_sentences -> build_tokenizer -> tokenize_sentences ->
# train_word2vec -> report -> (선택) save / plot_embeddings 순서로 위 함수들을 호출한다.
def main():
    parser = argparse.ArgumentParser(description="한국어 문장 Word2Vec 분석")
    parser.add_argument("--input", "-i", help="문장이 줄 단위로 담긴 텍스트 파일 경로 (미지정 시 샘플 문장 사용)")
    parser.add_argument("--words", "-w", nargs="*", help="유사도를 확인할 단어 목록")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--vector-size", type=int, default=100)
    parser.add_argument("--window", type=int, default=5)
    parser.add_argument("--min-count", type=int, default=1)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--sg", type=int, choices=[0, 1], default=1, help="0=CBOW, 1=Skip-gram")
    parser.add_argument("--save", help="학습된 모델을 저장할 경로")
    parser.add_argument("--plot", help="임베딩을 2D로 시각화해서 저장할 이미지 경로 (예: plot.png)")
    parser.add_argument("--plot-max-words", type=int, default=50, help="--words 미지정 시 시각화할 최대 단어 수")
    parser.add_argument(
        "--user-words",
        nargs="*",
        help="형태소 분석기에 등록할 신조어 목록. '단어', '단어:태그', '단어:태그:점수' 형식 (기본 태그 NNP)",
    )
    parser.add_argument(
        "--user-dict",
        help="형태소 분석기에 등록할 사용자 사전 파일 경로 (탭 구분: 단어\\t태그\\t점수)",
    )
    args = parser.parse_args()

    sentences = load_sentences(args.input)
    if len(sentences) < 2:
        sys.exit("문장이 너무 적습니다. 최소 2개 이상의 문장이 필요합니다.")

    tokenizer = build_tokenizer(args.user_words, args.user_dict)
    if tokenizer is None:
        if args.user_words or args.user_dict:
            sys.exit("kiwipiepy가 설치되어 있지 않아 --user-words/--user-dict를 사용할 수 없습니다.")
        print(
            "[경고] kiwipiepy가 설치되어 있지 않아 공백 기준으로 토큰화합니다. "
            "형태소 분석을 사용하려면 'pip install kiwipiepy'를 실행하세요."
        )
        tokenizer = simple_tokenize

    tokenized = tokenize_sentences(sentences, tokenizer)
    model = train_word2vec(tokenized, args.vector_size, args.window, args.min_count, args.epochs, args.sg)

    report(model, args.words, args.top_n)

    if args.save:
        model.save(args.save)
        print(f"\n모델 저장 완료: {args.save}")

    if args.plot:
        plot_embeddings(model, args.plot, args.words, args.plot_max_words)


if __name__ == "__main__":
    main()
