# On Mac — after updating any vault file:
cd /Users/SkonP/AI_Prompt
git add .
git commit -m "Update: [what changed]"
git push

# On Server — pull latest:
cd ~/openclaw-vault
git pull

# Run anytime to load context: 
ssh ubuntu@43.160.222.7 '~/trading-bot/load_context.sh'