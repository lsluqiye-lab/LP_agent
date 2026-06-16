import time
from main import _set_cooldown, _is_cooldown, _watchdog_cooldowns
_set_cooldown("MSFT", "OFFENSIVE_BREAKOUT_WS")
print(_watchdog_cooldowns)
print(_is_cooldown("MSFT", "OFFENSIVE_BREAKOUT_WS"))
