"""Post-publication momentum; direction comes exclusively from observed prices."""
from dataclasses import replace
from .core import PositionStrategy


class EventMomentum(PositionStrategy):
    def __init__(self, confirmation=5, holding=40, trailing=3., fee_gate=True, use_trailing=True):
        super().__init__(confirmation=confirmation, holding=holding, trailing=trailing,
                         fee_gate=fee_gate, use_trailing=use_trailing)
        self.confirmation=confirmation; self.holding=holding; self.trailing=trailing
        self.fee_gate=fee_gate; self.use_trailing=use_trailing
        self.answered=set()

    def decide(self, obs):
        orders=self.manage(obs,self.holding,self.trailing,self.use_trailing)
        for sym,tick in self.event_tick.items():
            if not self.eligible(obs,sym) or sym in self.plans or (sym,tick) in self.answered: continue
            # History includes only quotes observed BEFORE this decision.
            after=[p for t,p in self.history[sym] if t >= tick]
            age=obs.tick-tick
            if age < self.confirmation or len(after) < self.confirmation: continue
            if age > self.confirmation + 4: continue  # never chase a five-minute-old release
            q=obs.quotes[sym]
            move=q.mid/after[0]-1
            # Persistence estimate, not the announced pct or eventual target.
            expected=abs(move)*min(2.,self.holding/max(1,self.confirmation))
            slippage_budget=min(.012,max(.001,abs(move)/max(1,self.confirmation)*1.5))
            costs=2*obs.taker_fee + q.spread + slippage_budget + .001 + .001
            if self.fee_gate and expected <= costs: continue
            if abs(move) < .001: continue
            self.answered.add((sym,tick))
            entry=self.enter(obs,sym,1 if move > 0 else -1)
            for intent in entry:
                price=q.ask*(1+slippage_budget) if intent.side=="buy" else q.bid*(1-slippage_budget)
                orders.append(replace(intent,price=round(price,2)))
        return orders
