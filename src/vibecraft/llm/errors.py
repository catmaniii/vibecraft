"""LLM 层异常。所有都被 IntentParser 转成 ParseError 返回，不向外抛。"""

from __future__ import annotations


class LLMError(Exception):
    """LLM 通用基类。"""
