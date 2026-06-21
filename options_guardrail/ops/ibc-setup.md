# IB Gateway + IBC setup (Ubuntu, headless paper)

The executor connects to a running **IB Gateway in PAPER mode** on localhost:7497.
On a headless server you need Gateway to (a) start without a GUI and (b) re-login
after IBKR's daily restart. **IBC** (IBController) handles both.

## 1. Install IB Gateway (stable, offline installer)
```bash
sudo useradd -m -s /bin/bash guardrail        # dedicated user
sudo su - guardrail
# download the stable Gateway for Linux from IBKR, then:
chmod +x ibgateway-stable-standalone-linux-x64.sh
./ibgateway-stable-standalone-linux-x64.sh     # installs to ~/Jts/ibgateway/<ver>
```

## 2. Install IBC
```bash
# download IBC for Linux (github.com/IbcAlpha/IBC), then:
sudo mkdir -p /opt/ibc && sudo unzip IBCLinux-*.zip -d /opt/ibc
sudo chmod -R o-rwx /opt/ibc
```

## 3. Configure IBC — /opt/ibc/config.ini (key lines)
```
IbLoginId=your_paper_username        # the paper login, NOT the live one
IbPassword=your_paper_password
TradingMode=paper
IbDir=/home/guardrail/Jts
AcceptIncomingConnectionAction=accept
ReadOnlyApi=no                       # MUST be 'no' or orders are blocked
OverrideTwsApiPort=7497
# Auto-restart instead of full re-login when IBKR does its daily restart:
AutoRestartTime=11:45 PM             # set OUTSIDE your session window
```
Secure it: `chmod 600 /opt/ibc/config.ini` (it holds the paper password).

## 4. Run IBC under systemd (headless via xvfb)
Gateway needs an X display even headless; wrap with `xvfb-run`.
`/etc/systemd/system/ibc-gateway.service`:
```
[Unit]
Description=IB Gateway via IBC (paper)
After=network-online.target
Wants=network-online.target

[Service]
User=guardrail
Environment=DISPLAY=:0
ExecStart=/usr/bin/xvfb-run -a /opt/ibc/gatewaystart.sh
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```
```bash
sudo apt-get install -y xvfb
sudo systemctl enable --now ibc-gateway
```

## 5. Verify
```bash
ss -ltnp | grep 7497        # Gateway listening on the paper port
# then from the venv:
python3 - <<'PY'
from ib_async import IB
ib = IB(); ib.connect("127.0.0.1", 7497, clientId=99)
print("accounts:", ib.managedAccounts())   # expect a DU... paper account
ib.disconnect()
PY
```
If `managedAccounts()` returns a `DU...` id, the executor's paper gate will accept
it. If it shows a `U...` (live) id, STOP — you logged IBC into the wrong account.

## Gotchas
- **Daily restart**: keep `AutoRestartTime` and your session window from overlapping.
- **ReadOnlyApi=no** in IBC *and* Read-Only Access disabled in Client Portal.
- **One clientId per connection**: the executor uses IBKR_CLIENT_ID; don't reuse it
  for ad-hoc scripts while a session runs.
- **2FA**: paper logins don't require the IBKR mobile 2FA, which is exactly why we
  run paper unattended. Live would need IBKR's 2FA-less "second user" setup — out
  of scope until you're well past paper.
