from round2.bootstrap import audit
class Gateway(audit.CorrectedGateway):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.aggregate_gross=0.
    def sample_risk(self):
        super().sample_risk()
        self.aggregate_gross=max(self.aggregate_gross,sum(abs(q)*self.server.ref_prices.get(sym,0.) for a,p in self.server.portfolios.items() if a.startswith('alpha_') for sym,q in p.positions.items()))
