from tools.trading import GetHistoryOrdersTool

tool = GetHistoryOrdersTool()
print(tool.execute(days=5))
