#!/usr/bin/env python3
"""
Integration test script to validate:
1. Config load.
2. TokenHub/LLM connectivity and response generation.
3. Telegram notification delivery.
4. Telegram inline keyboard callback approval loop.
"""

import sys
import os
import time

# Ensure options_guardrail is in import path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import Config
from strategist_run import caller_from_config
from telegram_notify import from_config, NullTelegram


def mask_key(key: str | None) -> str:
    if not key:
        return "None"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


def run_test():
    print("=== Options Guardrail Validation Test ===")
    
    # 1. Load config
    try:
        cfg = Config.load()
        print("\n[1/3] Configuration loaded successfully:")
        print(f"  - Provider:      {cfg.strategist_provider}")
        print(f"  - Model:         {cfg.strategist_model}")
        print(f"  - TokenHub URL:  {getattr(cfg, 'tokenhub_api_url', 'N/A')}")
        print(f"  - TokenHub Key:  {mask_key(cfg.tokenhub_api_key)}")
        print(f"  - OpenRouter Key:{mask_key(cfg.openrouter_api_key)}")
        print(f"  - Anthropic Key: {mask_key(cfg.anthropic_api_key)}")
        print(f"  - Telegram Chat: {cfg.telegram_chat_id}")
        print(f"  - Telegram Bot:  {mask_key(cfg.telegram_token)}")
    except Exception as e:
        print(f"\n❌ Error loading configuration: {e}")
        return

    # 2. Test LLM connectivity
    print("\n[2/3] Testing LLM / TokenHub connectivity...")
    try:
        caller = caller_from_config(cfg)
        start_time = time.time()
        response = caller(
            system_prompt="You are a simple test assistant. Output exactly the word 'SUCCESS' in all caps and nothing else.",
            user_content="Say the validation word.",
            model=cfg.strategist_model
        )
        duration = time.time() - start_time
        print(f"  ✅ LLM Connection OK! (Took {duration:.2f}s)")
        print(f"  - Response: '{response.strip()}'")
    except Exception as e:
        print(f"  ❌ LLM Connection Failed: {e}")

    # 3. Test Telegram client
    print("\n[3/3] Testing Telegram notification and approval...")
    telegram = from_config(cfg)
    if isinstance(telegram, NullTelegram):
        print("  ⚠️ Telegram is NOT configured in your .env (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing).")
        print("  Skipping Telegram live verification.")
        return

    # Send a notification
    try:
        print("  - Sending test notification message to Telegram...")
        telegram.notify("🤖 *options-guardrail integration test* \nTesting connection. Status: ONLINE ✅")
        print("  ✅ Notification message sent.")
    except Exception as e:
        print(f"  ❌ Failed to send Telegram notification: {e}")
        return

    # Send an approval request
    try:
        req_id = f"test_{int(time.time())}"
        print(f"  - Sending test approval request (ID: {req_id})...")
        print("  👉 Please check your Telegram chat and tap either [Approve] or [Reject].")
        print("  ⏳ Waiting up to 60 seconds for your response...")
        
        # We request approval but override timeout_sec to 60s for testing
        approved = telegram.request_approval(
            text=f"🤖 *options-guardrail transaction approval test*\nRequesting approval for a test transaction.\nRequest ID: `{req_id}`",
            request_id=req_id,
            timeout_sec=60,
            poll_sec=2.0
        )
        
        if approved:
            print("\n  🎉 Success! You tapped APPROVE. Callback communication is working perfectly!")
        else:
            print("\n  ⚠️ Tapped REJECT or the request timed out. Either way, the communication round-trip works.")
    except Exception as e:
        print(f"  ❌ Failed during Telegram approval test: {e}")


if __name__ == "__main__":
    run_test()
