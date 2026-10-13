import importlib.util
from pathlib import Path

import os

def get_keys():
    k1 = os.environ.get("S2API_KEY1")
    k2 = os.environ.get("S2API_KEY2")
    if not k1 or not k2:
        # Fallback to reading from existing ch183 in DB if env not set
        import sqlite3
        conn = sqlite3.connect('file:C:/Users/zhugu/.new-api-local/new-api.db?mode=ro', uri=True)
        row = conn.execute("SELECT key FROM channels WHERE id = 183").fetchone()
        conn.close()
        if row and row[0]:
            lines = [k.strip() for k in row[0].split('\n') if k.strip()]
            if len(lines) >= 2:
                return lines[0], lines[1]
            return lines[0], lines[0]
        raise RuntimeError("S2API keys not provided via env or DB")
    return k1, k2

k1, k2 = get_keys()
spec = importlib.util.spec_from_file_location("smoke", Path("scripts/ops/newapi-local-smoke.py"))
assert spec is not None and spec.loader is not None
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

token, user_id = smoke.admin_auth()
headers = {"Authorization": f"Bearer {token}", "New-Api-User": str(user_id)}

# Fetch current channel 183
status, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/183", headers=headers)
print("Fetch ch183:", status, body.get("success") if isinstance(body, dict) else body)

ch_data = body["data"]

# Prepare update payload
# Models: deepseek-v4-flash, deepseek-v4.1-flash, glm-5.3-flash
models_str = "deepseek-v4-flash,deepseek-v4.1-flash,glm-5.3-flash"
multi_key_str = f"{k1}\n{k2}"

update_payload = {
    "id": 183,
    "name": "s2api-gateway",
    "type": 1, # OpenAI / Codex / OpenAI-compatible
    "key": multi_key_str,
    "base_url": "https://s2api.top",
    "other": "",
    "models": models_str,
    "group": "default",
    "model_mapping": "",
    "status_code_mapping": "",
    "headers": "",
    "priority": 25,
    "weight": 5,
    "auto_ban": 1,
    "test_model": "glm-5.3-flash",
    "channel_info": {
        "is_multi_key": True,
        "multi_key_size": 2,
        "multi_key_status_list": {},
        "multi_key_polling_index": 0,
        "multi_key_mode": "polling"
    }
}

status, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/", method="PUT", body=update_payload, headers=headers)
print("Update ch183:", status, body)

status, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/183/status", method="POST", body={"status": 1}, headers=headers)
print("Enable ch183:", status, body)

# Test via admin API
for test_m in ["glm-5.3-flash", "deepseek-v4-flash", "deepseek-v4.1-flash"]:
    status, body = smoke.http_json(f"{smoke.NEWAPI_BASE}/api/channel/test/183?model={test_m}", headers=headers)
    print(f"Admin test ch183 ({test_m}):", status, body.get("success"), body.get("time"))
