"""Supported chains (GeckoTerminal network ids), address validation, explorer links."""
import re

_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # base58, no 0 O I l

CHAINS = {
    "eth":         {"label": "Ethereum", "kind": "evm",    "explorer": "https://etherscan.io/address/"},
    "base":        {"label": "Base",     "kind": "evm",    "explorer": "https://basescan.org/address/"},
    "bsc":         {"label": "BNB Chain", "kind": "evm",   "explorer": "https://bscscan.com/address/"},
    "arbitrum":    {"label": "Arbitrum", "kind": "evm",    "explorer": "https://arbiscan.io/address/"},
    "polygon_pos": {"label": "Polygon",  "kind": "evm",    "explorer": "https://polygonscan.com/address/"},
    "solana":      {"label": "Solana",   "kind": "solana", "explorer": "https://solscan.io/account/"},
}


def is_supported_chain(chain: str) -> bool:
    return chain in CHAINS


def normalize_wallet(chain: str, wallet: str):
    """Return the canonical address string, or None if it is not a valid
    address for `chain`. EVM addresses are lower-cased (case carries no meaning
    there); Solana addresses are case-sensitive and left untouched."""
    if not isinstance(wallet, str) or chain not in CHAINS:
        return None
    wallet = wallet.strip()
    kind = CHAINS[chain]["kind"]
    if kind == "evm":
        return wallet.lower() if _EVM_RE.match(wallet) else None
    return wallet if _SOL_RE.match(wallet) else None


def explorer_url(chain: str, wallet: str):
    meta = CHAINS.get(chain)
    if not meta or normalize_wallet(chain, wallet) is None:
        return None
    return meta["explorer"] + wallet
