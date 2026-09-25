#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""RTVI (Real-Time Voice Interface) protocol implementation for Pipecat."""






__all__ = [
    "BotOutputTransformResult",
    "SpokenProgressData",
    "RTVIClientMessageFrame",
    "RTVIFunctionCallReportLevel",
    "RTVIObserver",
    "RTVIObserverParams",
    "RTVIProcessor",
    "RTVIServerMessageFrame",
    "RTVIServerResponseFrame",
    "RTVIUICancelJobGroupFrame",
    "RTVIUICommandFrame",
    "RTVIUIEventFrame",
    "RTVIUISnapshotFrame",
    "RTVIUIJobGroupFrame",
]


# Keep frame/model imports independent of optional transports and services.
_LAZY_EXPORTS = {'RTVIClientMessageFrame': ('pipecat.processors.frameworks.rtvi.frames', 'RTVIClientMessageFrame'), 'RTVIServerMessageFrame': ('pipecat.processors.frameworks.rtvi.frames', 'RTVIServerMessageFrame'), 'RTVIServerResponseFrame': ('pipecat.processors.frameworks.rtvi.frames', 'RTVIServerResponseFrame'), 'RTVIUICancelJobGroupFrame': ('pipecat.processors.frameworks.rtvi.frames', 'RTVIUICancelJobGroupFrame'), 'RTVIUICommandFrame': ('pipecat.processors.frameworks.rtvi.frames', 'RTVIUICommandFrame'), 'RTVIUIEventFrame': ('pipecat.processors.frameworks.rtvi.frames', 'RTVIUIEventFrame'), 'RTVIUIJobGroupFrame': ('pipecat.processors.frameworks.rtvi.frames', 'RTVIUIJobGroupFrame'), 'RTVIUISnapshotFrame': ('pipecat.processors.frameworks.rtvi.frames', 'RTVIUISnapshotFrame'), 'BotOutputTransformResult': ('pipecat.processors.frameworks.rtvi.models', 'BotOutputTransformResult'), 'SpokenProgressData': ('pipecat.processors.frameworks.rtvi.models', 'SpokenProgressData'), 'RTVIFunctionCallReportLevel': ('pipecat.processors.frameworks.rtvi.observer', 'RTVIFunctionCallReportLevel'), 'RTVIObserver': ('pipecat.processors.frameworks.rtvi.observer', 'RTVIObserver'), 'RTVIObserverParams': ('pipecat.processors.frameworks.rtvi.observer', 'RTVIObserverParams'), 'RTVIProcessor': ('pipecat.processors.frameworks.rtvi.processor', 'RTVIProcessor')}

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
