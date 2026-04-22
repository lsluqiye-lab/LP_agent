from agent.risk_manager import MacroRiskManager

rm = MacroRiskManager()
# Mock score and components
rm._last_score = 65.4
rm._last_regime = "normal"
rm._last_components = {"rsi_breadth": 82.35} # highly overbought

print("Test 1: Buy NVDA")
res1 = rm.approve_trade("BUY", "NVDA", 0.1)
print(res1)

print("\nTest 2: Buy AVGO (should be rejected due to same sub-sector 'Semi-Fabless')")
res2 = rm.approve_trade("BUY", "AVGO", 0.1)
print(res2)

print("\nTest 3: Buy AAPL (should be approved, different sub-sector)")
res3 = rm.approve_trade("BUY", "AAPL", 0.1)
print(res3)

