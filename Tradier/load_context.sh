KNOWLEDGE_DIR=~/trading-bot/knowledge

echo "=== OPENCLAW CONTEXT LOADER ==="
echo "Loading knowledge base..."
echo ""

echo "--- START HERE ---"
cat $KNOWLEDGE_DIR/OpenClaw/00_START_HERE.md
echo ""

echo "--- RULESET ---"
cat $KNOWLEDGE_DIR/OpenClaw/02_Ruleset_v4.md
echo ""

echo "--- WATCHLIST ---"
cat $KNOWLEDGE_DIR/OpenClaw/03_Watchlist.md
echo ""

echo "--- NEXT ACTIONS ---"
cat $KNOWLEDGE_DIR/OpenClaw/08_Next_Actions.md
echo ""

echo "--- NOVA SESSION PROMPT ---"
cat $KNOWLEDGE_DIR/templates/Nova_Session_Prompt.md
echo ""

echo "=== CONTEXT LOADED ==="
echo "Copy Nova Session Prompt above into Nova to begin."
