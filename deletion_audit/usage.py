"""Run instrumentation.  Any future model call MUST go through ``METER.record`` so the usage report stays truthful."""
from collections import defaultdict


class Meter:
    def __init__(self):
        self.models = defaultdict(lambda: {"provider": "", "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})

    def record(self, provider, model, input_tokens, output_tokens, cost_usd=0.0):
        m = self.models[model]
        m["provider"] = provider; m["calls"] += 1
        m["input_tokens"] += input_tokens; m["output_tokens"] += output_tokens; m["cost_usd"] += cost_usd

    def totals(self):
        return {"calls": sum(m["calls"] for m in self.models.values()),
                "input_tokens": sum(m["input_tokens"] for m in self.models.values()),
                "output_tokens": sum(m["output_tokens"] for m in self.models.values()),
                "cost_usd": sum(m["cost_usd"] for m in self.models.values())}


METER = Meter()
