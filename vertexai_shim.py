"""Compatibility shim for ragas.

ragas 0.4.3 unconditionally imports
``langchain_community.chat_models.vertexai`` at module load, but current
``langchain-community`` builds stripped that module (Vertex AI moved to the
standalone ``langchain-google-vertexai`` package). This project never uses
Vertex AI, so we register a stub that lets the import succeed.
"""

import sys
import types

import langchain_community
from langchain_community import chat_models


class _ChatVertexAI:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            'Vertex AI is not configured. This stub exists only to satisfy '
            'ragas\'s import of langchain_community.chat_models.vertexai.'
        )


def install():
    module = types.ModuleType('langchain_community.chat_models.vertexai')
    module.ChatVertexAI = _ChatVertexAI
    chat_models.vertexai = module
    sys.modules['langchain_community.chat_models.vertexai'] = module


install()
