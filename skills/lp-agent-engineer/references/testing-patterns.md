# LP-Agent Testing Patterns

## 1. Unit Testing
Test individual functions in `tools/` and `agent/` using `pytest`.
- **Mocking**: Use `unittest.mock` to prevent actual API calls to LongPort during unit tests.
- **Focus**: Slippage calculation, PRR math, risk scoring linear interpolation.

## 2. Integration Testing
Verify that tools can talk to the `BaseTradingEngine` adapters.
- Use `DRY_RUN=true` to test order submission without financial risk.

## 3. Regression Testing
Every fixed bug must have a test case in `tests/regression/`.
- `test_token_expiry.py`: Verify system behavior when token expires.
- `test_json_parsing.py`: Verify robust regex extraction of LLM JSON.

## 4. Performance & Concurrency
- `test_semaphore.py`: Verify that `asyncio.Semaphore` correctly limits expert calls to 2.
