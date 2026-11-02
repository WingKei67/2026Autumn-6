"""Causal strategy API. No engine handles, future paths or private event parameters."""
from collections import defaultdict, deque
from dataclasses import dataclass, field
import math
from statistics import mean, pstdev


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    bid_size: int
    ask_size: int

    @property
    def mid(self): return (self.bid + self.ask) / 2
    @property
    def spread(self): return (self.ask - self.bid) / self.mid
    @property
    def imbalance(self): return (self.bid_size - self.ask_size) / max(1, self.bid_size + self.ask_size)
    @property
    def microprice(self):
        return (self.bid * self.ask_size + self.ask * self.bid_size) / max(1, self.bid_size + self.ask_size)


@dataclass(frozen=True)
class PublicEvent:
    event: str
    tick: int
    symbols: tuple = ()
    kind: str = ''


@dataclass(frozen=True)
class Observation:
    tick: int
    quotes: dict
    positions: dict
    cash: float
    equity: float
    initial_cash: float
    halted: frozenset = frozenset()
    market_halted: bool = False
    session_open: bool = True
    events: tuple = ()
    taker_fee: float = .003
    maker_rebate: float = .001
    futures_enabled: bool = False
    cap: int = 150


@dataclass(frozen=True)
class Intent:
    symbol: str
    side: str
    quantity: int
    price: float
    order_type: str = 'ioc'
    cancel: bool = False
    group: str = ''
    reason: str = ''


class Strategy:
    def __init__(self, **params):
        self.params = params
        self.history = defaultdict(lambda: deque(maxlen=600))
        self.news = {}
        self.event_tick = {}
        self.resumed = {}
        self.halt_reference = {}

    def on_events(self, obs):
        for event in obs.events:
            if event.event == 'CALENDAR_EVENT':
                for sym in event.symbols:
                    self.news[sym] = (event.tick, event.kind)
                    self.event_tick[sym] = event.tick
            elif event.event == 'TRADING_HALT':
                for sym in event.symbols:
                    # Captured BEFORE the halt tick's quote enters history.
                    h = list(self.history[sym])
                    self.halt_reference[sym] = h[-90:-5] if len(h) >= 35 else []
            elif event.event == 'TRADING_RESUMED':
                for sym in event.symbols: self.resumed[sym] = event.tick

    def observe(self, obs):
        self.on_events(obs)
        result = self.decide(obs)
        for sym, q in obs.quotes.items():
            self.history[sym].append((obs.tick, q.mid))
        return result

    def decide(self, obs): return []

    @staticmethod
    def eligible(obs, sym):
        return (obs.session_open and not obs.market_halted and sym not in obs.halted
                and sym in obs.quotes and obs.quotes[sym].bid > 0
                and obs.quotes[sym].ask > obs.quotes[sym].bid)

    def volatility(self, sym, n=30):
        p = [p for _, p in list(self.history[sym])[-n-1:]]
        return pstdev([math.log(b/a) for a,b in zip(p,p[1:])]) if len(p) > 3 else 0.

    @staticmethod
    def cross(obs, sym, target, reason='', group=''):
        if sym not in obs.quotes: return []
        delta = int(target - obs.positions.get(sym, 0))
        if not delta: return []
        q = obs.quotes[sym]; side = 'buy' if delta > 0 else 'sell'
        # Fixed, observed limit: never reprice to a future quote at execution.
        price = q.ask * 1.001 if delta > 0 else q.bid * .999
        return [Intent(sym, side, abs(delta), round(price,2), reason=reason, group=group)]


@dataclass
class Plan:
    base: int
    target: int
    created: int
    entry: float
    started: int = 0
    best: float = 0.
    exiting: bool = False


class PositionStrategy(Strategy):
    def __init__(self, **params):
        super().__init__(**params)
        self.plans = {}
        self.last_entry = {}

    def manage(self, obs, holding=60, trailing=3., use_trailing=True):
        orders = []
        for sym, plan in list(self.plans.items()):
            if not self.eligible(obs, sym): continue
            pos = obs.positions.get(sym,0); q = obs.quotes[sym]
            sign = 1 if plan.target > plan.base else -1
            if not plan.started:
                if pos == plan.base and obs.tick - plan.created > 6:
                    del self.plans[sym]
                    continue
                if pos != plan.base:
                    plan.started = obs.tick; plan.entry = q.mid; plan.best = q.mid
            if plan.started:
                plan.best = max(plan.best,q.mid) if sign > 0 else min(plan.best,q.mid)
                stop = max(.004, min(.02, trailing * self.volatility(sym) * math.sqrt(10)))
                draw = sign * (q.mid / plan.best - 1)
                loss = sign * (q.mid / plan.entry - 1)
                if ((use_trailing and draw < -stop) or loss < -.015
                        or obs.tick - plan.started >= holding): plan.exiting = True
            target = plan.base if plan.exiting else plan.target
            if plan.exiting and pos == target:
                del self.plans[sym]
                continue
            orders += self.cross(obs,sym,target,'exit' if plan.exiting else 'entry')
        return orders

    def enter(self, obs, sym, direction, lot=8):
        base = obs.positions.get(sym,0)
        target = min(obs.cap, base + lot) if direction > 0 else max(0,base-lot)
        if target == base: return []
        self.plans[sym] = Plan(base,target,obs.tick,obs.quotes[sym].mid)
        self.last_entry[sym] = obs.tick
        return self.cross(obs,sym,target,'entry')
