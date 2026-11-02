"""Offline-testable alpha components; importing never starts a live bot."""
from .event_momentum import EventMomentum
from .post_halt import PostHaltReversion
from .market_making import AdaptiveMarketMaker
from .basket import BasketArbitrage

STRATEGIES = {'event_momentum': EventMomentum, 'post_halt': PostHaltReversion,
              'market_making': AdaptiveMarketMaker, 'basket': BasketArbitrage}
