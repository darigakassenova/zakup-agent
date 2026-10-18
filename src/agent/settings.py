import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

PORTAL = os.getenv("PORTAL_URL", "https://goszakup.gov.kz")
CONTRACT_LINK = PORTAL + "/ru/egzcontract/cpublic/show/{id}"
ANNOUNCE_LINK = PORTAL + "/ru/announce/index/{id}"

DATA_FROM = "2024-01-01"
TOP_K = 10
MAX_TOP_K = 50
DEVIATION_LIMIT = 0.30
MIN_GROUP_SIZE = 5
VOLUME_MIN_RATIO = 1.5
PEER_EXAMPLES = 5

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-5-5")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "")
LLM_TIMEOUT = 60
