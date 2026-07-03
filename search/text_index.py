# -*- coding: utf-8 -*-
"""외부 엔진 없이 쓰는 작은 한글 친화 검색 색인 (순수 파이썬).

이 프로젝트는 데이터가 적어(수만 건 이하) 메모리 n-gram 역색인이면 충분하다. 그래서
Elasticsearch(JVM·외부 클러스터·월과금)나 SQLite FTS5의 제약 없이 직접 만든다.
  - ES: Vercel 서버리스엔 상주 서버가 없어 외부 클러스터가 필요 → 이 규모엔 과함.
  - FTS5 trigram: 3자 미만 질의를 못 잡는다("강남"·"한화" 같은 2자 한글 검색이 빈 결과).
기본 n=2(bigram)라 2자 한글 검색이 바로 되고, Vercel 서버리스(읽기전용 FS)에서도 그대로 돈다.

쓰임:
    idx = TextIndex()
    idx.add("롯데캐슬 드메르")     # key 생략 시 텍스트 자체가 key
    idx.add("강남역 5번출구", key="역:강남역")
    idx.search("캐슬")             # -> {"롯데캐슬 드메르"}
"""
import re
from collections import defaultdict

_WS = re.compile(r"\s+")


def normalize(s):
    """검색 정규화: 공백 전부 제거 + 소문자화. 한글은 그대로 둔다.

    공백을 지워 '롯데캐슬 드메르'를 '드메르'·'롯데캐슬'로도, '롯데 캐슬' 질의로도 잡히게 한다."""
    return _WS.sub("", (s or "").lower())


def ngrams(s, n):
    """정규화 문자열의 n-gram 목록(중복 포함). 길이가 n 미만이면 통째로 하나만."""
    if len(s) < n:
        return [s] if s else []
    return [s[i:i + n] for i in range(len(s) - n + 1)]


class TextIndex:
    """n-gram 역색인. 같은 key로 여러 번 add하면 처음 것만 색인한다(중복 무시)."""

    def __init__(self, n=2):
        self.n = n
        self._norm = {}                 # key -> 정규화 텍스트(substring 재확인용)
        self._post = defaultdict(set)   # gram -> {key}

    def add(self, text, key=None):
        key = text if key is None else key
        if key in self._norm:
            return
        norm = normalize(text)
        self._norm[key] = norm
        for g in set(ngrams(norm, self.n)):
            self._post[g].add(key)

    def __len__(self):
        return len(self._norm)

    def search(self, q):
        """q를 부분일치하는 key 집합.

        n-gram 교집합으로 후보를 좁힌 뒤, 실제 substring 포함으로 재확인해 오탐을 제거한다
        (교집합은 gram이 다 있어도 '연속'을 보장하지 않으므로 재확인이 필요)."""
        qn = normalize(q)
        if not qn:
            return set()
        if len(qn) < self.n:
            # 질의가 n보다 짧아(예: 1자) 색인을 못 쓰면 전수 substring으로 처리.
            return {k for k, t in self._norm.items() if qn in t}
        cand = None
        for g in set(ngrams(qn, self.n)):
            posting = self._post.get(g)
            if not posting:
                return set()            # 없는 gram이 하나라도 있으면 매치 불가
            cand = posting if cand is None else (cand & posting)
            if not cand:
                return set()
        return {k for k in cand if qn in self._norm[k]}
