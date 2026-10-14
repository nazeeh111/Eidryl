"""Declared public constructors. No target-effect rules live here."""
import collections
import io

def text(initial):
    return io.StringIO(initial)

def sequence(values, capacity):
    return collections.deque(values, maxlen=capacity)

def encoded():
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-16", write_through=True)
