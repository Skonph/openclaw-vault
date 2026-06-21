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
