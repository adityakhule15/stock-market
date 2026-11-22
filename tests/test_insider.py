from marketdata.insider import parse_pit_xbrl, summarize_insider
from unittest.mock import patch


SAMPLE_XBRL = """<?xml version="1.0" encoding="UTF-8"?>
<xbrl>
  <NameOfThePerson contextRef="Disclosure1">Alice Promoter</NameOfThePerson>
  <CategoryOfPerson contextRef="Disclosure1">Promoter</CategoryOfPerson>
  <SecuritiesAcquiredOrDisposedTransactionType contextRef="Disclosure1">Buy</SecuritiesAcquiredOrDisposedTransactionType>
  <ModeOfAcquisitionOrDisposal contextRef="Disclosure1">Market Purchase</ModeOfAcquisitionOrDisposal>
  <SecuritiesAcquiredOrDisposedNumberOfSecurity contextRef="Disclosure1">1000</SecuritiesAcquiredOrDisposedNumberOfSecurity>
  <SecuritiesAcquiredOrDisposedValueOfSecurity contextRef="Disclosure1">250000</SecuritiesAcquiredOrDisposedValueOfSecurity>
  <NameOfThePerson contextRef="Disclosure2">Bob Director</NameOfThePerson>
  <CategoryOfPerson contextRef="Disclosure2">Director</CategoryOfPerson>
  <SecuritiesAcquiredOrDisposedTransactionType contextRef="Disclosure2">Sell</SecuritiesAcquiredOrDisposedTransactionType>
  <ModeOfAcquisitionOrDisposal contextRef="Disclosure2">Market Sale</ModeOfAcquisitionOrDisposal>
  <SecuritiesAcquiredOrDisposedNumberOfSecurity contextRef="Disclosure2">200</SecuritiesAcquiredOrDisposedNumberOfSecurity>
  <SecuritiesAcquiredOrDisposedValueOfSecurity contextRef="Disclosure2">50000</SecuritiesAcquiredOrDisposedValueOfSecurity>
  <NameOfThePerson contextRef="Disclosure3">Carol</NameOfThePerson>
  <CategoryOfPerson contextRef="Disclosure3">Promoter</CategoryOfPerson>
  <SecuritiesAcquiredOrDisposedTransactionType contextRef="Disclosure3">Buy</SecuritiesAcquiredOrDisposedTransactionType>
  <ModeOfAcquisitionOrDisposal contextRef="Disclosure3">ESOP</ModeOfAcquisitionOrDisposal>
  <SecuritiesAcquiredOrDisposedNumberOfSecurity contextRef="Disclosure3">5000</SecuritiesAcquiredOrDisposedNumberOfSecurity>
  <SecuritiesAcquiredOrDisposedValueOfSecurity contextRef="Disclosure3">999999</SecuritiesAcquiredOrDisposedValueOfSecurity>
</xbrl>
"""


def test_parse_pit_xbrl_extracts_disclosures():
    rows = parse_pit_xbrl(SAMPLE_XBRL)
    assert len(rows) == 3
    assert rows[0]["person"] == "Alice Promoter"
    assert rows[0]["quantity"] == 1000


def test_summarize_ignores_esop_and_nets_market_flow():
    with (
        patch("marketdata.insider.list_pit_filings", return_value=[{"xmlFileName": "x.xml"}]),
        patch("marketdata.insider.fetch_xbrl", return_value=SAMPLE_XBRL),
    ):
        summary = summarize_insider("RELIANCE")
    # Buy 250000 - Sell 50000; ESOP ignored
    assert summary["net_value_inr"] == 200000.0
    assert summary["flag"] == "buy"
    assert summary["vote"] == 0.5
    assert summary["txns_n"] == 2
