import os

from dotenv import load_dotenv

load_dotenv()

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# How many verification rounds the extraction loop may run. Each round is one
# extra Claude call (render extracted data -> compare against original -> fix),
# so this caps both latency and cost. The loop only keeps going while Claude
# keeps reporting mismatches, so this is a ceiling, not a fixed number of calls.
MAX_VERIFICATION_ROUNDS = int(os.getenv("MAX_VERIFICATION_ROUNDS", "3"))
