#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#









__all__ = [
    "BaseUserTurnStartStrategy",
    "ExternalUserTurnStartStrategy",
    "KrispVivaIPUserTurnStartStrategy",
    "MinWordsUserTurnStartStrategy",
    "TranscriptionUserTurnStartStrategy",
    "UserTurnStartedParams",
    "VADUserTurnStartStrategy",
    "WakePhraseUserTurnStartStrategy",
]


# Keep frame/model imports independent of optional transports and services.
_LAZY_EXPORTS = {'BaseUserTurnStartStrategy': ('pipecat.turns.user_start.base_user_turn_start_strategy', 'BaseUserTurnStartStrategy'), 'UserTurnStartedParams': ('pipecat.turns.user_start.base_user_turn_start_strategy', 'UserTurnStartedParams'), 'ExternalUserTurnStartStrategy': ('pipecat.turns.user_start.external_user_turn_start_strategy', 'ExternalUserTurnStartStrategy'), 'MinWordsUserTurnStartStrategy': ('pipecat.turns.user_start.min_words_user_turn_start_strategy', 'MinWordsUserTurnStartStrategy'), 'TranscriptionUserTurnStartStrategy': ('pipecat.turns.user_start.transcription_user_turn_start_strategy', 'TranscriptionUserTurnStartStrategy'), 'VADUserTurnStartStrategy': ('pipecat.turns.user_start.vad_user_turn_start_strategy', 'VADUserTurnStartStrategy'), 'WakePhraseUserTurnStartStrategy': ('pipecat.turns.user_start.wake_phrase_user_turn_start_strategy', 'WakePhraseUserTurnStartStrategy'), 'KrispVivaIPUserTurnStartStrategy': ('pipecat.turns.user_start.krisp_viva_ip_user_turn_start_strategy', 'KrispVivaIPUserTurnStartStrategy')}

def __getattr__(name):
    import importlib
    if name not in _LAZY_EXPORTS:
        raise AttributeError(name)
    module, attribute = _LAZY_EXPORTS[name]
    try:
        value = getattr(importlib.import_module(module), attribute)
    except ImportError:
        if name != 'KrispVivaIPUserTurnStartStrategy':
            raise
        value = None
    globals()[name] = value
    return value
