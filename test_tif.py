from tools.trading import BuyStockTool
tool = BuyStockTool()
# We expect this to fail due to authentication or other API reasons but we can check the logs to see if it sets GTC.
print("Testing with GTC modifications...")
