# -*- coding: utf-8 -*-
"""프로젝트 검색 모듈.

앱의 텍스트 검색(건물명·역명 등)은 전부 이 패키지의 색인을 거친다. 외부 검색엔진 없이
순수 파이썬 n-gram 역색인으로 구현한다 — 근거·설계는 text_index.py 참고.
"""
from .text_index import TextIndex, ngrams, normalize

__all__ = ["TextIndex", "ngrams", "normalize"]
