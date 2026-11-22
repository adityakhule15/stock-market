"""Short glossary for tooltips."""

TERMS = {
    "stop": "The price where you exit to limit a loss. Place this in Groww as soon as you buy.",
    "target": "The price where you take profit. Net of Groww charges must still be positive.",
    "charges": "Brokerage, STT, exchange fees, GST, stamp, and (for delivery sells) demat/DP.",
    "dp": "Demat debit charge on a delivery (CNC) sell — often ~₹20 + GST on Groww.",
    "lot": "Options trade in fixed lots. Cost = premium × lot size.",
    "premium": "The price of one option unit. You pay premium × lot size for one lot.",
    "practice": "Tracked like a real trade but does not move your wallet. Used to prove an edge.",
    "expected": "Model chance × win after charges − (1 − chance) × loss. Must clear a minimum rupee bar.",
    "drawdown": "How far your balance has fallen from the start (or from a peak).",
    "ready": "Cleared the risk gate and charges. You may copy levels to Groww.",
    "watch": "Interesting but below the bar — do not place it.",
}


def term(key: str) -> str:
    return TERMS.get(key, "")
