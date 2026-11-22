import pytest

from config import trading_config as cfg
from portfolio.models import PortfolioSettings
from portfolio.services import arm_kill_switch, get_portfolio_state


@pytest.mark.django_db
def test_kill_switch_blocks_state():
    arm_kill_switch(True)
    pf = get_portfolio_state()
    assert pf.kill_switch is True
    arm_kill_switch(False)


@pytest.mark.django_db
def test_kill_equity_auto_arms():
    ps = PortfolioSettings.get_solo()
    ps.equity = cfg.KILL_EQUITY
    ps.life_status = PortfolioSettings.LifeStatus.ALIVE
    ps.kill_switch = False
    ps.save()
    pf = get_portfolio_state()
    assert pf.kill_switch is True
    # KILL_EQUITY (1500) is in survival band
    assert pf.life_status == "survival"
