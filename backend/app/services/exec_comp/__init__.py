"""Execution-price compensation (成交价差补偿), MT5 v1.

SSOT: docs/exec-compensation/. Layers (03 §4.1):
  classify.py / calc.py  pure functions, no IO
  source.py              on-demand replica reads (01 D21) behind ``FillSource``
  limits.py              server-wide query slots + time budget
  query.py               the query core: Query -> Result, caller-agnostic
Routes only authenticate and turn HTTP params into a ``Query``.
"""
