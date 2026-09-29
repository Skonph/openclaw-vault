
To show config
cat /home/ubuntu/.openclaw/openclaw.json

To show with masking
cat ~/.openclaw/openclaw.json | sed -E 's/(apiKey|token|secret|password|api_key|key|API_KEY)":\s*"[^"]*"/\1": "***"/gi'