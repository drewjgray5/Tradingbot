"""
Guardrail Wrapper - Re-exports from execution (consolidated path).

All trade logic lives in execution.py. This module re-exports GuardrailWrapper
for backward compatibility. New code should use execution.place_order() or
client.get_client().place_order().
"""

from execution import GuardrailWrapper

__all__ = ["GuardrailWrapper"]
