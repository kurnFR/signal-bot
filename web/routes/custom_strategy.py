import json
import re
import time
import logging
import math
import threading
from typing import List, Dict, Any, Optional, Union
from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field, field_validator, model_validator

import requests
from backtest.strategies import STRATEGIES
from backtest.custom_strategy import (
    _create_strategy_runner, _eval_condition, _normalize_indicator_name,
)
from backtest.params import BASE_PARAMS
from backtest.metrics import compute_metrics
from db.custom_strategy_store import StrategyInUseError, list_definitions, mutate_definition
from db.db import fetch_ohlcv_with_features_df
from web.auth import log_audit
from config import (
    LLM_PROVIDER,
    NINEROUTER_API_KEY, NINEROUTER_BASE_URL, NINEROUTER_MODEL,
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL,
    ANTHROPIC_API_KEY, ANTHROPIC_API_URL, ANTHROPIC_MODEL,
)
from web.routes.backtest import STRATEGY_METADATA

router = APIRouter(prefix="/api/custom-strategy", tags=["custom-strategy"])
logger = logging.getLogger("web.custom_strategy")

VALID_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,20}$")
VALID_MARKETS = {"spot", "futures"}
VALID_TIMEFRAMES = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}
MAX_AI_HISTORY_MESSAGES = 6
MAX_AI_HISTORY_MESSAGE_CHARS = 2_000
MAX_AI_CURRENT_CONFIG_CHARS = 8_000
MAX_AI_CONTEXT_CHARS = 16_000

_REGISTRY_LOCK = threading.Lock()
_BUILT_IN_NAMES = frozenset(STRATEGIES)


# Metadata dictionary of all indicators available in the features dataset
INDICATOR_DEFINITIONS = {
    "rsi": {
        "name": "rsi",
        "label": "RSI (14)",
        "category": "Momentum",
        "description": "Relative Strength Index (0-100). Oversold < 30-35, Overbought > 65-70.",
        "default": 30.0,
        "type": "numeric",
    },
    "macd": {
        "name": "macd",
        "label": "MACD Line",
        "category": "Trend",
        "description": "Fast EMA (12) minus Slow EMA (26).",
        "default": 0.0,
        "type": "numeric",
    },
    "macd_signal": {
        "name": "macd_signal",
        "label": "MACD Signal Line",
        "category": "Trend",
        "description": "9-period EMA of MACD Line.",
        "default": 0.0,
        "type": "numeric",
    },
    "macd_hist": {
        "name": "macd_hist",
        "label": "MACD Histogram",
        "category": "Momentum",
        "description": "MACD minus Signal line. > 0 indicates bullish momentum acceleration; < 0 bearish.",
        "default": 0.0,
        "type": "numeric",
    },
    "volume_ratio": {
        "name": "volume_ratio",
        "label": "Volume Ratio",
        "category": "Volume",
        "description": "Bar volume divided by 20-period volume SMA. > 1.0 above average, > 1.8-2.5 surge.",
        "default": 1.5,
        "type": "numeric",
    },
    "volume_sma": {
        "name": "volume_sma",
        "label": "Volume SMA (20)",
        "category": "Volume",
        "description": "20-period rolling average of trading volume.",
        "default": 1000.0,
        "type": "numeric",
    },
    "atr": {
        "name": "atr",
        "label": "ATR (14)",
        "category": "Volatility",
        "description": "Average True Range in price units.",
        "default": 100.0,
        "type": "numeric",
    },
    "atr_pct": {
        "name": "atr_pct",
        "label": "ATR %",
        "category": "Volatility",
        "description": "ATR expressed as a percentage of closing price (e.g. 1.5%).",
        "default": 1.5,
        "type": "numeric",
    },
    "close": {
        "name": "close",
        "label": "Close Price",
        "category": "Price",
        "description": "Current candle close price.",
        "default": 50000.0,
        "type": "numeric",
    },
    "support": {
        "name": "support",
        "label": "Support Level",
        "category": "Support/Resistance",
        "description": "Lowest low over trailing 50 bars.",
        "default": 48000.0,
        "type": "numeric",
    },
    "resistance": {
        "name": "resistance",
        "label": "Resistance Level",
        "category": "Support/Resistance",
        "description": "Highest high over trailing 50 bars.",
        "default": 52000.0,
        "type": "numeric",
    },
    "swing_high": {
        "name": "swing_high",
        "label": "Swing High",
        "category": "Fibonacci",
        "description": "Recent 50-bar swing high.",
        "default": 52000.0,
        "type": "numeric",
    },
    "swing_low": {
        "name": "swing_low",
        "label": "Swing Low",
        "category": "Fibonacci",
        "description": "Recent 50-bar swing low.",
        "default": 48000.0,
        "type": "numeric",
    },
    "fib_236": {
        "name": "fib_236",
        "label": "Fibonacci 0.236",
        "category": "Fibonacci",
        "description": "Fibonacci 23.6% retracement level.",
        "default": 51000.0,
        "type": "numeric",
    },
    "fib_382": {
        "name": "fib_382",
        "label": "Fibonacci 0.382",
        "category": "Fibonacci",
        "description": "Fibonacci 38.2% retracement level.",
        "default": 50000.0,
        "type": "numeric",
    },
    "fib_5": {
        "name": "fib_5",
        "label": "Fibonacci 0.500",
        "category": "Fibonacci",
        "description": "Fibonacci 50.0% midpoint retracement level.",
        "default": 49500.0,
        "type": "numeric",
    },
    "fib_618": {
        "name": "fib_618",
        "label": "Fibonacci 0.618",
        "category": "Fibonacci",
        "description": "Fibonacci 61.8% golden pocket retracement level.",
        "default": 49000.0,
        "type": "numeric",
    },
    "fib_786": {
        "name": "fib_786",
        "label": "Fibonacci 0.786",
        "category": "Fibonacci",
        "description": "Fibonacci 78.6% deep retracement level.",
        "default": 48500.0,
        "type": "numeric",
    },
}

class RuleCondition(BaseModel):
    indicator: str = Field(..., max_length=100, json_schema_extra={"example": "rsi"})
    operator: str = Field(..., max_length=2, json_schema_extra={"example": "<"})
    value: Union[float, int, str] = Field(..., json_schema_extra={"example": 30.0})


class CustomStrategyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80, json_schema_extra={"example": "rsi_volume_surge_v1"})
    displayName: str = Field(..., min_length=1, max_length=200, json_schema_extra={"example": "RSI + Volume Surge"})
    description: str = Field("Custom rule-based strategy", max_length=2000, json_schema_extra={"example": "Fades RSI extremes with volume confirmation"})
    rationale: Optional[str] = Field("", max_length=2000, json_schema_extra={"example": "Combines mean reversion with institutional volume surge."})
    longConditions: List[RuleCondition] = Field(default_factory=list, max_length=100)
    shortConditions: List[RuleCondition] = Field(default_factory=list, max_length=100)
    slAtrMult: float = Field(1.5, ge=0.5, le=5.0)
    riskRewardRatio: float = Field(2.5, ge=1.0, le=6.0)
    useTrailingStop: bool = Field(False)
    trailActivationR: float = Field(1.0, ge=0.25, le=5.0)
    trailDistanceAtrMult: float = Field(1.5, ge=0.5, le=5.0)


class GenerateAIStrategyRequest(BaseModel):
    prompt: str = Field(..., min_length=3, max_length=5000, json_schema_extra={"example": "Create a momentum strategy that buys when RSI < 35 and volume is 2x average, short when RSI > 65 with 1.5 ATR stop and 2.5 RR"})
    history: Optional[List[Dict[str, str]]] = Field(default=None, max_length=50)
    currentConfig: Optional[Dict[str, Any]] = Field(default=None)

    @field_validator("history")
    @classmethod
    def bound_history(cls, history):
        if history is None:
            return None
        # The generation handler consumes only the newest six messages.
        bounded = history[-MAX_AI_HISTORY_MESSAGES:]
        for message in bounded:
            if len(message.get("content", "")) > MAX_AI_HISTORY_MESSAGE_CHARS:
                raise ValueError("Each AI history message must be at most 2000 characters")
        return bounded

    @field_validator("currentConfig")
    @classmethod
    def bound_current_config(cls, current_config):
        if current_config is not None and len(json.dumps(current_config, separators=(",", ":"), ensure_ascii=True)) > MAX_AI_CURRENT_CONFIG_CHARS:
            raise ValueError("Current strategy configuration is too large")
        return current_config

    @model_validator(mode="after")
    def bound_total_ai_context(self):
        history_chars = sum(len(message.get("content", "")) for message in (self.history or []))
        config_chars = len(json.dumps(self.currentConfig, separators=(",", ":"), ensure_ascii=True)) if self.currentConfig is not None else 0
        if len(self.prompt) + history_chars + config_chars > MAX_AI_CONTEXT_CHARS:
            raise ValueError("AI strategy context exceeds the 16000-character limit")
        return self


class QuickTestRequest(BaseModel):
    strategy: CustomStrategyRequest
    symbol: str = Field("BTCUSDT")
    market: str = Field("spot")
    timeframe: str = Field("1h")

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, symbol):
        normalized = symbol.strip().upper()
        if not VALID_SYMBOL_PATTERN.fullmatch(normalized):
            raise ValueError("Symbol must contain only 2-20 letters or digits")
        return normalized

    @field_validator("market")
    @classmethod
    def validate_market(cls, market):
        normalized = market.strip().lower()
        if normalized not in VALID_MARKETS:
            raise ValueError("Market must be one of: spot, futures")
        return normalized

    @field_validator("timeframe")
    @classmethod
    def validate_timeframe(cls, timeframe):
        normalized = timeframe.strip()
        if normalized not in VALID_TIMEFRAMES:
            raise ValueError("Unsupported timeframe")
        return normalized


def load_custom_strategies():
    """Refresh web runners and metadata using the committed shared definitions."""
    try:
        with _REGISTRY_LOCK:
            saved = list_definitions()
            runners = {}
            metadata = {}
            for s in saved:
                if not s["enabled"]:
                    continue
                name = s["name"]
                if name in _BUILT_IN_NAMES or f"inverse_{name}" in _BUILT_IN_NAMES:
                    continue
                runners[name], runners[f"inverse_{name}"] = _create_strategy_runner(s)
                metadata[name] = {
                    "displayName": s.get("displayName", name),
                    "category": "Custom AI Strategy",
                    "validated": False,
                    "description": s.get("description", ""),
                    "rationale": s.get("rationale", ""),
                    "longConditions": s.get("longConditions", []),
                    "shortConditions": s.get("shortConditions", []),
                    "useTrailingStop": s.get("useTrailingStop", False),
                    "params": [
                        {"key": "CUSTOM_SL_ATR_MULT", "label": "Stop-Loss (ATR Multiple)", "type": "float", "default": s.get("slAtrMult", 1.5), "min": 0.5, "max": 5.0, "step": 0.25},
                        {"key": "CUSTOM_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": s.get("riskRewardRatio", 2.5), "min": 1.0, "max": 6.0, "step": 0.5},
                    ]
                }
            for name, runner in STRATEGIES.copy().items():
                if getattr(runner, "__module__", None) == _create_strategy_runner.__module__:
                    STRATEGIES.pop(name, None)
                    STRATEGY_METADATA.pop(name, None)
            STRATEGIES.update(runners)
            STRATEGY_METADATA.update(metadata)
    except Exception as exc:
        logger.warning("Could not refresh custom strategy registry (%s)", type(exc).__name__)
        # An unavailable or invalid snapshot must never keep disabled runners active.
        with _REGISTRY_LOCK:
            for name, runner in STRATEGIES.copy().items():
                if getattr(runner, "__module__", None) == _create_strategy_runner.__module__:
                    STRATEGIES.pop(name, None)
                    STRATEGY_METADATA.pop(name, None)


def _call_openai_compatible_api(url: str, api_key: str, model: str, messages: list, timeout: Union[int, float, tuple] = (3.0, 25.0)) -> Optional[str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "model": model,
        "max_tokens": 1200,
        "temperature": 0.2,
        "messages": messages,
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        if resp.status_code == 200:
            raw_text = resp.text.strip()
            if "data: [DONE]" in raw_text:
                raw_text = raw_text.split("data: [DONE]")[0].strip()
            data = json.loads(raw_text)
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            content = msg.get("content")
            if content is None or content == "":
                content = msg.get("reasoning") or ""

            if isinstance(content, dict):
                return json.dumps(content)
            elif isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict):
                        parts.append(item.get("text") or str(item))
                    else:
                        parts.append(str(item))
                return "\n".join(parts).strip()
            return str(content).strip()
        else:
            logger.warning(f"LLM request to {url} returned status {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"LLM request to {url} failed: {e}")
    return None


def _call_anthropic_api(api_key: str, model: str, system_prompt: str, user_prompt: str, timeout: int = 25) -> Optional[str]:
    if not api_key:
        return None
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": 1200,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    try:
        resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            for block in data.get("content", []):
                if block.get("type") == "text":
                    return block.get("text", "").strip()
    except Exception as e:
        logger.warning(f"Anthropic API call failed: {e}")
    return None


def _parse_llm_json(raw_text: Any) -> Optional[dict]:
    if not raw_text:
        return None
    if isinstance(raw_text, dict):
        return raw_text
    text = str(raw_text).strip()

    # Match ```json ... ``` blocks
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()

    # Find boundaries of JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]

    try:
        return json.loads(text)
    except Exception:
        # Try basic cleanup of trailing commas
        cleaned = re.sub(r",\s*([\]}])", r"\1", text)
        try:
            return json.loads(cleaned)
        except Exception:
            return None


def _heuristic_strategy_generator(prompt: str) -> dict:
    """Intelligent offline fallback that converts user prompt text into a strategy config."""
    p = prompt.lower()
    name_parts = ["ai"]
    disp_parts = []
    long_rules = []
    short_rules = []

    sl_atr = 1.5
    rr = 2.5
    use_trailing = "trailing" in p or "trail" in p

    # RSI logic
    if "rsi" in p:
        name_parts.append("rsi")
        disp_parts.append("RSI")
        # Extract potential custom RSI numbers
        rsi_match = re.search(r"rsi\s*(?:<|below|under)?\s*(\d{2})", p)
        rsi_val = float(rsi_match.group(1)) if rsi_match else 30.0
        long_rules.append({"indicator": "rsi", "operator": "<", "value": rsi_val})
        short_rules.append({"indicator": "rsi", "operator": ">", "value": round(100.0 - rsi_val, 1)})

    # Volume logic
    if "volume" in p or "vol" in p or "surge" in p or "spike" in p:
        name_parts.append("volume")
        disp_parts.append("Volume Surge")
        vol_match = re.search(r"(?:volume|vol)\s*(?:>|above|over|x)?\s*(\d+(?:\.\d+)?)", p)
        vol_val = float(vol_match.group(1)) if vol_match else 1.8
        long_rules.append({"indicator": "volume_ratio", "operator": ">", "value": vol_val})
        short_rules.append({"indicator": "volume_ratio", "operator": ">", "value": vol_val})

    # MACD logic
    if "macd" in p or "momentum" in p:
        name_parts.append("macd")
        disp_parts.append("MACD Momentum")
        long_rules.append({"indicator": "macd_hist", "operator": ">", "value": 0.0})
        short_rules.append({"indicator": "macd_hist", "operator": "<", "value": 0.0})

    # Support / Resistance / Breakout
    if "support" in p or "bounce" in p or "rebound" in p:
        name_parts.append("sr_bounce")
        disp_parts.append("Support Rebound")
        long_rules.append({"indicator": "close", "operator": ">", "value": "support"})
        short_rules.append({"indicator": "close", "operator": "<", "value": "resistance"})
    elif "breakout" in p or "break" in p:
        name_parts.append("breakout")
        disp_parts.append("Breakout")
        long_rules.append({"indicator": "close", "operator": ">", "value": "resistance"})
        short_rules.append({"indicator": "close", "operator": "<", "value": "support"})

    # Check for RR / SL mentions
    rr_match = re.search(r"(?:rr|risk\s*reward|r:r)\s*(?:of|ratio)?\s*(\d+(?:\.\d+)?)", p)
    if not rr_match:
        rr_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:x\s*)?(?:rr|r:r|risk\s*reward)", p)
    if rr_match:
        try:
            rr = float(rr_match.group(1))
        except ValueError:
            pass

    sl_match = re.search(r"(?:sl|stop|stop-loss)\s*(?:of)?\s*(\d+(?:\.\d+)?)\s*(?:atr)?", p)
    if not sl_match:
        sl_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:x\s*)?atr\s*(?:stop|sl|loss)?", p)
    if sl_match:
        try:
            sl_atr = float(sl_match.group(1))
        except ValueError:
            pass

    # Default to RSI if nothing was matched
    if not long_rules:
        name_parts.append("momentum_filter")
        disp_parts.append("Momentum Filter")
        long_rules.append({"indicator": "rsi", "operator": "<", "value": 35.0})
        long_rules.append({"indicator": "volume_ratio", "operator": ">", "value": 1.5})
        short_rules.append({"indicator": "rsi", "operator": ">", "value": 65.0})
        short_rules.append({"indicator": "volume_ratio", "operator": ">", "value": 1.5})

    name_str = "_".join(name_parts) + "_v1"
    disp_str = " + ".join(disp_parts) if disp_parts else "AI Quantitative Strategy"

    return {
        "name": name_str,
        "displayName": disp_str,
        "description": f"Quantitative strategy configured from prompt: '{prompt[:100]}'",
        "rationale": f"Architected using quantitative momentum and volume confirmation rules with a {sl_atr}x ATR stop and {rr}x Risk:Reward ratio.",
        "longConditions": long_rules,
        "shortConditions": short_rules,
        "slAtrMult": sl_atr,
        "riskRewardRatio": rr,
        "useTrailingStop": use_trailing,
        "trailActivationR": 1.0,
        "trailDistanceAtrMult": 1.5,
        "chatResponse": f"I have synthesized a quantitative trading strategy '{disp_str}' with {len(long_rules)} long conditions, {len(short_rules)} short conditions, a {sl_atr} ATR stop-loss, and a {rr} Risk:Reward ratio.",
    }


def _build_system_prompt() -> str:
    indicators_summary = "\n".join([
        f"- `{k}`: {v['description']} (Category: {v['category']})"
        for k, v in INDICATOR_DEFINITIONS.items()
    ])

    return f"""You are an elite quantitative crypto trading strategist and algorithmic designer.
Your mission is to translate user ideas, trading styles, and natural language descriptions into a precise algorithmic strategy specification that can be simulated directly on historical crypto market data.

AVAILABLE MARKET INDICATORS (only use these exact names):
{indicators_summary}

VALID OPERATORS:
`<`, `<=`, `>`, `>=`, `==`

CONDITION RULES:
- `indicator`: Must be one of the available indicator names above.
- `operator`: One of `<`, `<=`, `>`, `>=`, `==`.
- `value`: Either a numeric constant (e.g. 30.0, 1.8, 0.0) OR another indicator name (e.g. "macd_signal", "support", "resistance") for dynamic comparisons like `macd > macd_signal` or `close > support`.

RISK MANAGEMENT PARAMETERS:
- `slAtrMult`: Stop Loss distance as an ATR multiple (typically 1.0 to 3.0, allowed range 0.5 to 5.0).
- `riskRewardRatio`: Take Profit distance relative to stop loss (typically 1.5 to 3.5, allowed range 1.0 to 6.0).
- `useTrailingStop`: boolean (true or false).
- `trailActivationR`: Profit in R-multiples to trigger trailing stop (typically 1.0 to 2.0).
- `trailDistanceAtrMult`: Trailing stop distance in ATR multiples (typically 1.0 to 2.0).

STRICT OUTPUT FORMAT:
You MUST respond with valid JSON ONLY. No markdown conversational preface, no markdown epilogue outside the JSON.
Your JSON must strictly match this structure:
{{
  "name": "short_unique_snake_case_id_v1",
  "displayName": "Clean Human Readable Name",
  "description": "Concise summary of entry and exit logic",
  "rationale": "Quantitative justification for these indicators, thresholds, and risk parameters",
  "longConditions": [
    {{"indicator": "rsi", "operator": "<", "value": 30.0}},
    {{"indicator": "volume_ratio", "operator": ">", "value": 1.8}}
  ],
  "shortConditions": [
    {{"indicator": "rsi", "operator": ">", "value": 70.0}},
    {{"indicator": "volume_ratio", "operator": ">", "value": 1.8}}
  ],
  "slAtrMult": 1.5,
  "riskRewardRatio": 2.5,
  "useTrailingStop": false,
  "trailActivationR": 1.0,
  "trailDistanceAtrMult": 1.5,
  "chatResponse": "Conversational reply explaining the strategy, how it behaves in trending vs ranging markets, and tips for backtesting it."
}}
"""


@router.get("/indicators")
def get_indicators():
    return {
        "indicators": list(INDICATOR_DEFINITIONS.values()),
        "operators": ["<", "<=", ">", ">=", "=="],
    }


@router.get("/list")
def list_custom_strategies():
    try:
        return {"strategies": list_definitions(include_deployments=True)}
    except Exception as exc:
        logger.warning("Could not list custom strategies (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Saved strategies are unavailable.") from exc


@router.post("/generate-ai")
def generate_ai_strategy(req: GenerateAIStrategyRequest):
    user_prompt = req.prompt.strip()
    if not user_prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    system_prompt = _build_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]

    # Append conversation history if provided
    if req.history:
        for msg in req.history[-6:]:
            role = "user" if msg.get("role") == "user" else "assistant"
            messages.append({"role": role, "content": msg.get("content", "")})

    # Add current draft context if user is refining
    if req.currentConfig:
        context_note = f"Current active draft strategy configuration to refine: {json.dumps(req.currentConfig)}"
        messages.append({"role": "system", "content": context_note})

    messages.append({"role": "user", "content": user_prompt})

    raw_response = None
    provider_used = "Offline Quantitative Engine"

    # Try Primary Provider
    if LLM_PROVIDER == "9router" and NINEROUTER_BASE_URL:
        raw_response = _call_openai_compatible_api(
            url=f"{NINEROUTER_BASE_URL.rstrip('/')}/chat/completions",
            api_key=NINEROUTER_API_KEY,
            model=NINEROUTER_MODEL,
            messages=messages,
            timeout=(2.5, 20.0),
        )
        if raw_response:
            provider_used = f"9Router ({NINEROUTER_MODEL.split('/')[-1]})"

    # Fallback to OpenRouter if primary didn't succeed
    if not raw_response and OPENROUTER_API_KEY:
        raw_response = _call_openai_compatible_api(
            url=f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions",
            api_key=OPENROUTER_API_KEY,
            model=OPENROUTER_MODEL,
            messages=messages,
            timeout=(3.5, 25.0),
        )
        if raw_response:
            provider_used = f"OpenRouter ({OPENROUTER_MODEL.split('/')[-1]})"

    # Fallback to Anthropic if available
    if not raw_response and ANTHROPIC_API_KEY:
        raw_response = _call_anthropic_api(
            api_key=ANTHROPIC_API_KEY,
            model=ANTHROPIC_MODEL,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        if raw_response:
            provider_used = f"Anthropic ({ANTHROPIC_MODEL})"

    # Parse response
    parsed = _parse_llm_json(raw_response) if raw_response else None

    # Fallback to intelligent heuristic if parsing failed
    if not parsed:
        logger.info("Using heuristic quantitative generator for user prompt")
        parsed = _heuristic_strategy_generator(user_prompt)
        provider_used = "AI Quantitative Heuristic Engine"

    # Normalize fields
    clean_name = re.sub(r"[^a-z0-9_]", "_", str(parsed.get("name", "custom_strategy")).lower()).strip("_")
    if not clean_name:
        clean_name = f"ai_strat_{int(time.time())}"

    # Normalize conditions
    def _normalize_conds(conds):
        result = []
        if not isinstance(conds, list):
            return result
        for c in conds:
            if not isinstance(c, dict):
                continue
            ind = _normalize_indicator_name(c.get("indicator", "rsi"))
            op = str(c.get("operator", "<"))
            if op not in ("<", "<=", ">", ">=", "==", "!="):
                op = "<"
            val = c.get("value", 30.0)
            if isinstance(val, str):
                normalized_val = _normalize_indicator_name(val)
                if normalized_val in INDICATOR_DEFINITIONS:
                    val = normalized_val
                else:
                    try:
                        val = float(val)
                    except ValueError:
                        val = 30.0
            else:
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    val = 30.0
            result.append({"indicator": ind, "operator": op, "value": val})
        return result

    long_conds = _normalize_conds(parsed.get("longConditions", []))
    short_conds = _normalize_conds(parsed.get("shortConditions", []))

    sl_atr = float(parsed.get("slAtrMult", 1.5))
    sl_atr = max(0.5, min(5.0, sl_atr))

    rr_ratio = float(parsed.get("riskRewardRatio", 2.5))
    rr_ratio = max(1.0, min(6.0, rr_ratio))

    use_trailing = bool(parsed.get("useTrailingStop", False))
    trail_act = float(parsed.get("trailActivationR", 1.0))
    trail_dist = float(parsed.get("trailDistanceAtrMult", 1.5))

    strat_obj = {
        "name": clean_name,
        "displayName": parsed.get("displayName") or clean_name.replace("_", " ").title(),
        "description": parsed.get("description") or f"AI generated strategy based on: {user_prompt}",
        "rationale": parsed.get("rationale") or "Parameters calculated to balance win rate and profit expectancy.",
        "longConditions": long_conds,
        "shortConditions": short_conds,
        "slAtrMult": sl_atr,
        "riskRewardRatio": rr_ratio,
        "useTrailingStop": use_trailing,
        "trailActivationR": trail_act,
        "trailDistanceAtrMult": trail_dist,
    }

    chat_reply = parsed.get("chatResponse") or (
        f"I've designed **{strat_obj['displayName']}** for you based on your prompt! "
        f"It evaluates {len(long_conds)} condition(s) for long entries and {len(short_conds)} for short entries, "
        f"paired with a {sl_atr}x ATR stop and {rr_ratio}x Risk:Reward target."
    )

    return {
        "status": "success",
        "provider": provider_used,
        "strategy": strat_obj,
        "chatResponse": chat_reply,
    }


_NAME_PATH = Path(..., max_length=80, pattern=r"^[a-z0-9_]+$")


def _validated_definition(req: CustomStrategyRequest, name: str) -> dict:
    if not name or name.startswith("inverse_") or name in _BUILT_IN_NAMES or f"inverse_{name}" in _BUILT_IN_NAMES:
        raise HTTPException(status_code=400, detail="Choose an available custom strategy name.")
    if not req.displayName.strip() or not (req.longConditions or req.shortConditions):
        raise HTTPException(status_code=400, detail="A display name and at least one rule are required.")
    definition = req.model_dump()
    definition["name"] = name
    # Validate both indicator operands before a definition can reach any runner.
    for rule in definition["longConditions"] + definition["shortConditions"]:
        indicator = _normalize_indicator_name(rule["indicator"])
        if indicator not in INDICATOR_DEFINITIONS or rule["operator"] not in {"<", "<=", ">", ">=", "==", "!="}:
            raise HTTPException(status_code=400, detail="Every rule needs a supported indicator and operator.")
        value = rule["value"]
        if isinstance(value, str) and len(value) > 100:
            raise HTTPException(status_code=400, detail="Rule comparison value is too long.")
        comparison = _normalize_indicator_name(value) if isinstance(value, str) else None
        if comparison in INDICATOR_DEFINITIONS:
            value = comparison
        else:
            try:
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=400, detail="Rule values must be finite numbers or supported indicators.") from exc
        rule.update(indicator=indicator, value=value)
    return definition


def _mutate_strategy(request: Request, name: str, action: str, **kwargs):
    try:
        found = mutate_definition(name, action, **kwargs)
    except StrategyInUseError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "deployments": exc.deployments}) from exc
    except Exception as exc:
        logger.warning("Custom strategy change failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not save the custom strategy.") from exc
    if not found and action != "delete":
        raise HTTPException(status_code=404, detail="Custom strategy not found.")
    if found:
        load_custom_strategies()
        verb = ("enabled" if kwargs["enabled"] else "disabled") if action == "toggle" else {
            "create": "saved", "update": "updated", "delete": "deleted",
        }[action]
        user = request.state.user
        log_audit(
            f"CUSTOM_STRATEGY_{verb.upper()}", user["username"], user.get("id"),
            {"name": name, "force": kwargs.get("force", False)},
            request.client.host if request.client else "unknown",
        )
    else:
        verb = "deleted"
    return {"status": "success", "name": name, "message": f"Strategy {name} {verb}."}


@router.post("/create")
def create_custom_strategy(req: CustomStrategyRequest, request: Request):
    name = re.sub(r"[^a-z0-9_]", "_", req.name.strip().lower()).strip("_")
    definition = _validated_definition(req, name)
    result = _mutate_strategy(request, name, "create", definition=definition)
    return {**result, "displayName": req.displayName}


@router.put("/{name}")
def update_custom_strategy(req: CustomStrategyRequest, request: Request, name: str = _NAME_PATH):
    if name != req.name:
        raise HTTPException(status_code=400, detail="The strategy name cannot be changed.")
    return _mutate_strategy(request, name, "update", definition=_validated_definition(req, name))


class StrategyEnabledRequest(BaseModel):
    enabled: bool = Field(strict=True)


@router.patch("/{name}/enabled")
def toggle_custom_strategy(req: StrategyEnabledRequest, request: Request, name: str = _NAME_PATH, force: bool = False):
    return _mutate_strategy(request, name, "toggle", enabled=req.enabled, force=force)


@router.delete("/{name}")
def delete_custom_strategy(request: Request, name: str = _NAME_PATH):
    return _mutate_strategy(request, name, "delete")


@router.post("/quick-test")
def quick_test_strategy(req: QuickTestRequest):
    """Fast simulation on requested symbol and timeframe to preview strategy metrics."""
    df = fetch_ohlcv_with_features_df(req.symbol, req.market, req.timeframe, closed_only=True)
    if df is None or len(df) < 50:
        raise HTTPException(status_code=400, detail=f"Insufficient candles for {req.symbol} {req.timeframe}")

    # Use the latest 1,000 candles for rapid responsive preview
    test_df = df.tail(1000).copy().reset_index(drop=True)

    def_dict = req.strategy.model_dump()
    run_fn, _ = _create_strategy_runner(def_dict)

    params = dict(BASE_PARAMS)
    params["CUSTOM_SL_ATR_MULT"] = req.strategy.slAtrMult
    params["CUSTOM_RISK_REWARD_RATIO"] = req.strategy.riskRewardRatio

    import math

    def _clean_num(val, default=0.0):
        if val is None or not isinstance(val, (int, float)):
            return default
        if math.isinf(val) or math.isnan(val):
            return None
        return round(float(val), 4)

    trades = run_fn(test_df, params)
    metrics = compute_metrics(trades)

    r_multiples = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    net_r = _clean_num(round(sum(r_multiples), 2))
    win_rate_pct = _clean_num(metrics.get("win_rate_pct"), default=0.0)
    win_rate_fraction = round(win_rate_pct / 100.0, 4) if win_rate_pct is not None else None

    return {
        "status": "success",
        "symbol": req.symbol,
        "market": req.market,
        "timeframe": req.timeframe,
        "totalCandles": len(test_df),
        "totalTrades": metrics.get("total_trades", len(trades)),
        "winRate": win_rate_fraction,
        "winRatePct": win_rate_pct,
        "netR": net_r,
        "profitFactor": _clean_num(metrics.get("profit_factor")),
        "maxDrawdownR": _clean_num(metrics.get("max_drawdown_r"), 0.0),
        "expectancyR": _clean_num(metrics.get("expectancy_r")),
    }
