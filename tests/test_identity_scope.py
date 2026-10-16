from datetime import timedelta
import unittest
from market_checker_app.agents import AgentContext,EntityRegistryAgent
from market_checker_app.services.identity_scope_service import IdentityScope
from market_checker_app.services.reviewed_identity_service import clock
from market_checker_app.utils.ticker_universe import load_canonical_tickers


class IdentityScopeTests(unittest.TestCase):
    def test_exclusions_are_dated_and_do_not_edit_the_canonical_universe(self):
        scope=IdentityScope.load();tickers=load_canonical_tickers()
        when=max(clock(r['known_at']) for r in scope.exclusions.values())
        self.assertEqual(0,scope.report(tickers,as_of=when-timedelta(microseconds=1))['excluded_count'])
        report=scope.report(tickers,as_of=when)
        self.assertEqual((685,2),(report['included_count'],report['excluded_count']))
        self.assertEqual({'OZK','LEG'},set(report['excluded']))
        self.assertEqual(687,len(load_canonical_tickers()))
        result=EntityRegistryAgent().run(AgentContext('scope',tuple(tickers),when))
        self.assertEqual((685,0,2),(result.metadata['resolved_identities'],
            result.metadata['unresolved_identities'],result.metadata['excluded_identities']))
        self.assertEqual(687,len(result.entities))

    def test_excluded_instrument_cannot_pass_the_signal_gate(self):
        from market_checker_app.agents import OrchestratorAgent,PredictionV21AdapterAgent,QualityGateAgent
        from unittest.mock import patch
        import pandas as pd
        scope=IdentityScope.load();when=max(clock(r['known_at']) for r in scope.exclusions.values())
        runner=OrchestratorAgent(shadow_mode=True)
        for agent in [EntityRegistryAgent(),PredictionV21AdapterAgent(),QualityGateAgent()]:runner.register(agent)
        with patch('market_checker_app.agents.orchestrator.utc_now',return_value=when):
            result=runner.run(watchlist=['OZK'],state={'signals':pd.DataFrame([{'ticker':'OZK','action':'NO_TRADE','forecast':'FLAT'}])})
        codes=[issue['code'] for check in result.quality_checks for issue in check.metadata['rejects']]
        self.assertIn('excluded_identity_scope',codes)
