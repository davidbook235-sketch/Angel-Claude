import os
import core

cfg = dict(os.environ)
p = dict(core.DEFAULTS)
uni = core.load_universe()
try:
    api, src = core.angel_login(cfg), "Angel One"
except Exception as e:
    print("Angel login fail, yfinance use kar raha hu:", e)
    api, src = None, "Yahoo (yfinance)"
res = core.scan_all(uni, p, src, api)
print(res.to_string())
core.tg_send(cfg["TG_TOKEN"], cfg["TG_CHAT_ID"], core.format_msg(res))
