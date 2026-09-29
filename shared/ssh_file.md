/Users/SkonP/.ssh/id_ed25519

public_key
cat ~/.ssh/id_ed25519.pub

private_key
cat ~/.ssh/id_ed25519

On your new computer:

1. Create a file at `~/.ssh/id_ed25519` (using `nano ~/.ssh/id_ed25519` or any editor).
2. Paste the exact block of text you copied (from private_key including _`-----BEGIN OPENSSH PRIVATE KEY-----` and _`-----END OPENSSH PRIVATE KEY-----`)
3. **CRITICAL STEP (Permissions):** You must restrict the permissions of the private key file, or SSH will block you from using it. Run this command on your new station's terminal:
    
    bash
    
    chmod 600 ~/.ssh/id_ed25519


**fix the SSH problem properly so scp works.** The handshake race persists because fail2ban bans reactively and attackers rotate IPs faster than it bans. The real fix is the Tencent **security group** — restrict port 22 inbound to your IP, which drops attack traffic at the network edge _before_ it ever reaches sshd. The dynamic-IP concern I raised earlier is manageable: you can set it to your current IP now (get it with `curl -s ifconfig.me` on the Mac), and if your ISP rotates it later, you edit the rule in the console (which doesn't depend on SSH). Once port 22 only accepts your IP, scp goes through instantly and stays reliable. This is the durable fix and I'd lean toward it — the brute-force noise has now cost you most of a deploy session.