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

# How many tool calls the verification agent may make within a single round
# (zoom_tool, re_extract_points, ...). The agent decides whether to call tools
# at all and how many times; this only caps runaway tool use.
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "6"))

# DEBUG ONLY — default 0 (off). When set to N, the verification pass reports a
# forced correction in rounds 1..N-1 so the multi-round loop can be observed
# deterministically on any chart (round N uses the real verdict). Keep it
# <= MAX_VERIFICATION_ROUNDS + 1. Has zero effect when 0.
FORCE_VERIFY_ROUNDS = int(os.getenv("FORCE_VERIFY_ROUNDS", "0"))
