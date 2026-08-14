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

# now in the database tab tehre are many errors that first the user-added table which is added by users appears to be very small which is hard to read and its in pill which is more hard to read make it something that it can be read make it bigger and dont make its chip or pill like and make the delete button proper functionable for user-added table only