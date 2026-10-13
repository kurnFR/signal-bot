"""Stablecoin base-token detection for GeckoTerminal pool metadata.

Only pools whose BASE token is a stablecoin are excluded. Stablecoin quote pairs
such as ARK/USDT remain eligible because the tracked token is ARK, not USDT.
Symbols provide broad coverage; canonical addresses protect common deployments
when symbols are missing or ambiguous.
"""

import re

_STABLE_SYMBOLS = {
    "USDT", "USDC", "USDC.E", "USDBC", "USDT.E", "USDT0", "DAI", "BUSD",
    "FDUSD", "TUSD", "USDP", "PYUSD", "USDE", "USDS", "USDD", "USD1",
    "FRAX", "LUSD", "GUSD", "USDX", "UST", "USTC", "EURC", "EURS",
    "EUROC", "AGEUR", "XDAI", "SUSD", "USD0", "USDM", "USDR",
}
_STABLE_SYMBOLS_COMPACT = {re.sub(r"[^A-Z0-9]", "", s) for s in _STABLE_SYMBOLS}

# Canonical common stablecoin contracts. Symbol checks intentionally supplement
# this list because providers may omit addresses or expose newer deployments.
_STABLE_ADDRESSES = {
    "eth": {
        "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
        "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
        "0x6c3ea9036406852006290770bedfcaba0e23a0e8",  # PYUSD
    },
    "base": {
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
        "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca",  # USDbC
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI
    },
    "bsc": {
        "0x55d398326f99059ff775485246999027b3197955",  # USDT
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC
        "0xe9e7cea3dedca5984780bafc599bd69add087d56",  # BUSD
        "0xc5f0f7b66764f6ec8c8dff7ba683102295e16409",  # FDUSD
    },
    "solana": {
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    },
    "arbitrum": {
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # USDT
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # USDC
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1",  # DAI
    },
    "polygon_pos": {
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",  # USDT
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",  # USDC
        "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063",  # DAI
    },
}


def is_stablecoin_base_pool(network, pool):
    """True only when the pool's base token is identified as a stablecoin."""
    symbol = str(pool.get("base_token_symbol") or "").strip().upper()
    compact_symbol = re.sub(r"[^A-Z0-9]", "", symbol)
    if symbol in _STABLE_SYMBOLS or compact_symbol in _STABLE_SYMBOLS_COMPACT:
        return True
    address = str(pool.get("base_token_address") or "").strip()
    if network != "solana":
        address = address.lower()
    return address in _STABLE_ADDRESSES.get(network, set())
