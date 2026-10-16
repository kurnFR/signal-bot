import json
import os
import re
import time
import logging
from typing import List, Dict, Any, Optional, Union
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import requests
from backtest.strategies import STRATEGIES
from backtest.simulate import simulate
from backtest.params import BASE_PARAMS
from backtest.metrics import compute_metrics
from db.db import fetch_ohlcv_with_features_df
from config import (
    LLM_PROVIDER,
    NINEROUTER_API_KEY, NINEROUTER_BASE_URL, NINEROUTER_MODEL,
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL,
    ANTHROPIC_API_KEY, ANTHROPIC_API_URL, ANTHROPIC_MODEL,
)
from web.routes.backtest import STRATEGY_METADATA

router = APIRouter(prefix="/api/custom-strategy", tags=["custom-strategy"])
logger = logging.getLogger("web.custom_strategy")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CUSTOM_FILE = os.path.join(BASE_DIR, "custom_strategies.json")

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

INDICATOR_ALIASES = {
    "relative_strength_index": "rsi",
    "rsi_14": "rsi",
    "volume": "volume_ratio",
    "vol": "volume_ratio",
    "vol_ratio": "volume_ratio",
    "volume_multiplier": "volume_ratio",
    "macd_histogram": "macd_hist",
    "histogram": "macd_hist",
    "hist": "macd_hist",
    "macd_line": "macd",
    "signal_line": "macd_signal",
    "price": "close",
    "supp": "support",
    "res": "resistance",
    "fib_50": "fib_5",
    "fib_500": "fib_5",
    "fib_05": "fib_5",
}


def _normalize_indicator_name(name: str) -> str:
    cleaned = str(name).strip().lower()
    return INDICATOR_ALIASES.get(cleaned, cleaned)


class RuleCondition(BaseModel):
    indicator: str = Field(..., json_schema_extra={"example": "rsi"})
    operator: str = Field(..., json_schema_extra={"example": "<"})
    value: Union[float, int, str] = Field(..., json_schema_extra={"example": 30.0})


class CustomStrategyRequest(BaseModel):
    name: str = Field(..., json_schema_extra={"example": "rsi_volume_surge_v1"})
    displayName: str = Field(..., json_schema_extra={"example": "RSI + Volume Surge"})
    description: str = Field("Custom rule-based strategy", json_schema_extra={"example": "Fades RSI extremes with volume confirmation"})
    rationale: Optional[str] = Field("", json_schema_extra={"example": "Combines mean reversion with institutional volume surge."})
    longConditions: List[RuleCondition] = Field(default_factory=list)
    shortConditions: List[RuleCondition] = Field(default_factory=list)
    slAtrMult: float = Field(1.5, ge=0.5, le=5.0)
    riskRewardRatio: float = Field(2.5, ge=1.0, le=6.0)
    useTrailingStop: bool = Field(False)
    trailActivationR: float = Field(1.0, ge=0.25, le=5.0)
    trailDistanceAtrMult: float = Field(1.5, ge=0.5, le=5.0)


class GenerateAIStrategyRequest(BaseModel):
    prompt: str = Field(..., json_schema_extra={"example": "Create a momentum strategy that buys when RSI < 35 and volume is 2x average, short when RSI > 65 with 1.5 ATR stop and 2.5 RR"})
    history: Optional[List[Dict[str, str]]] = Field(default=None)
    currentConfig: Optional[Dict[str, Any]] = Field(default=None)


class QuickTestRequest(BaseModel):
    strategy: CustomStrategyRequest
    symbol: str = Field("BTCUSDT")
    market: str = Field("spot")
    timeframe: str = Field("1h")


def _eval_condition(row, prev_row, cond: dict) -> bool:
    ind = _normalize_indicator_name(cond.get("indicator", ""))
    val = cond.get("value")
    op = cond.get("operator", "==")

    current_val = row.get(ind)
    if current_val is None or (isinstance(current_val, float) and current_val != current_val):
        return False

    target_val = None
    if isinstance(val, str):
        normalized_val_indicator = _normalize_indicator_name(val)
        if normalized_val_indicator in row and not (isinstance(row.get(normalized_val_indicator), float) and row.get(normalized_val_indicator) != row.get(normalized_val_indicator)):
            target_val = row.get(normalized_val_indicator)
        else:
            try:
                target_val = float(val)
            except (ValueError, TypeError):
                return False
    else:
        try:
            target_val = float(val)
        except (ValueError, TypeError):
            return False

    if target_val is None or (isinstance(target_val, float) and target_val != target_val):
        return False

    c_val = float(current_val)
    t_val = float(target_val)

    if op == "<":
        return c_val < t_val
    elif op == "<=":
        return c_val <= t_val
    elif op == ">":
        return c_val > t_val
    elif op == ">=":
        return c_val >= t_val
    elif op == "==":
        return abs(c_val - t_val) < 1e-6
    elif op == "!=":
        return abs(c_val - t_val) >= 1e-6
    return False


def _create_strategy_runner(def_dict: dict):
    long_conds = def_dict.get("longConditions", [])
    short_conds = def_dict.get("shortConditions", [])
    sl_mult = float(def_dict.get("slAtrMult", 1.5))
    rr = float(def_dict.get("riskRewardRatio", 2.5))
    use_trailing = bool(def_dict.get("useTrailingStop", False))
    trail_act = float(def_dict.get("trailActivationR", 1.0))
    trail_dist = float(def_dict.get("trailDistanceAtrMult", 1.5))

    def long_condition(row, prev_row, params):
        if not long_conds:
            return False
        return all(_eval_condition(row, prev_row, c if isinstance(c, dict) else c.dict()) for c in long_conds)

    def short_condition(row, prev_row, params):
        if not short_conds:
            return False
        return all(_eval_condition(row, prev_row, c if isinstance(c, dict) else c.dict()) for c in short_conds)

    def stop_target(direction, entry_price, atr, row, params):
        cur_sl_mult = params.get("CUSTOM_SL_ATR_MULT", sl_mult)
        cur_rr = params.get("CUSTOM_RISK_REWARD_RATIO", rr)
        stop_dist = cur_sl_mult * atr
        target_dist = stop_dist * cur_rr
        if direction == "LONG":
            return entry_price - stop_dist, entry_price + target_dist
        else:
            return entry_price + stop_dist, entry_price - target_dist

    def run(df, params):
        p = dict(params)
        if use_trailing:
            p["USE_TRAILING_STOP"] = True
            p["TRAIL_ACTIVATION_R"] = trail_act
            p["TRAIL_DISTANCE_ATR_MULT"] = trail_dist
        return simulate(df, p, long_condition, short_condition, stop_target)

    def run_inverse(df, params):
        p = dict(params)
        if use_trailing:
            p["USE_TRAILING_STOP"] = True
            p["TRAIL_ACTIVATION_R"] = trail_act
            p["TRAIL_DISTANCE_ATR_MULT"] = trail_dist
        return simulate(df, p, short_condition, long_condition, stop_target)

    return run, run_inverse


def load_custom_strategies():
    if not os.path.exists(CUSTOM_FILE):
        return
    try:
        with open(CUSTOM_FILE, "r") as f:
            saved = json.load(f)
        for s in saved:
            name = s.get("name")
            if not name:
                continue
            run_fn, run_inv_fn = _create_strategy_runner(s)
            STRATEGIES[name] = run_fn
            STRATEGIES[f"inverse_{name}"] = run_inv_fn
            STRATEGY_METADATA[name] = {
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
            logger.info(f"Loaded custom strategy: {name}")
    except Exception as e:
        logger.error(f"Failed to load custom strategies: {e}")


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
    saved = []
    if os.path.exists(CUSTOM_FILE):
        try:
            with open(CUSTOM_FILE, "r") as f:
                saved = json.load(f)
        except Exception as e:
            logger.error(f"Error reading {CUSTOM_FILE}: {e}")
    return {"strategies": saved}


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


@router.post("/create")
def create_custom_strategy(req: CustomStrategyRequest):
    clean_name = re.sub(r"[^a-z0-9_]", "_", req.name.strip().lower()).strip("_")
    if not clean_name:
        raise HTTPException(status_code=400, detail="Strategy name cannot be empty")

    def_dict = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    def_dict["name"] = clean_name

    run_fn, run_inv_fn = _create_strategy_runner(def_dict)
    STRATEGIES[clean_name] = run_fn
    STRATEGIES[f"inverse_{clean_name}"] = run_inv_fn

    long_list = [c.model_dump() if hasattr(c, "model_dump") else c.dict() for c in req.longConditions]
    short_list = [c.model_dump() if hasattr(c, "model_dump") else c.dict() for c in req.shortConditions]

    STRATEGY_METADATA[clean_name] = {
        "displayName": req.displayName or clean_name,
        "category": "Custom AI Strategy",
        "validated": False,
        "description": req.description,
        "rationale": req.rationale or "",
        "longConditions": long_list,
        "shortConditions": short_list,
        "useTrailingStop": req.useTrailingStop,
        "params": [
            {"key": "CUSTOM_SL_ATR_MULT", "label": "Stop-Loss (ATR Multiple)", "type": "float", "default": req.slAtrMult, "min": 0.5, "max": 5.0, "step": 0.25},
            {"key": "CUSTOM_RISK_REWARD_RATIO", "label": "Risk:Reward Ratio", "type": "float", "default": req.riskRewardRatio, "min": 1.0, "max": 6.0, "step": 0.5},
        ]
    }

    # Persist to JSON file
    saved = []
    if os.path.exists(CUSTOM_FILE):
        try:
            with open(CUSTOM_FILE, "r") as f:
                saved = json.load(f)
        except Exception:
            saved = []

    # Update or append
    saved = [s for s in saved if s.get("name") != clean_name]
    saved.append(def_dict)
    with open(CUSTOM_FILE, "w") as f:
        json.dump(saved, f, indent=2)

    return {
        "status": "success",
        "name": clean_name,
        "displayName": req.displayName,
        "message": f"Strategy {clean_name} saved and registered successfully!"
    }


@router.delete("/{name}")
def delete_custom_strategy(name: str):
    clean_name = name.strip().lower()
    STRATEGIES.pop(clean_name, None)
    STRATEGIES.pop(f"inverse_{clean_name}", None)
    STRATEGY_METADATA.pop(clean_name, None)

    saved = []
    if os.path.exists(CUSTOM_FILE):
        try:
            with open(CUSTOM_FILE, "r") as f:
                saved = json.load(f)
        except Exception:
            saved = []

    saved = [s for s in saved if s.get("name") != clean_name]
    with open(CUSTOM_FILE, "w") as f:
        json.dump(saved, f, indent=2)

    return {"status": "success", "message": f"Strategy {clean_name} deleted."}


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

    trades = run_fn(test_df, params)
    metrics = compute_metrics(trades)

    r_multiples = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    net_r = round(sum(r_multiples), 2)
    win_rate_pct = metrics.get("win_rate_pct")
    win_rate_fraction = round(win_rate_pct / 100.0, 4) if win_rate_pct is not None else 0.0

    import math

    def _clean_num(val, default=0.0):
        if val is None or not isinstance(val, (int, float)):
            return default
        if math.isinf(val) or math.isnan(val):
            return None
        return round(float(val), 4)

    return {
        "status": "success",
        "symbol": req.symbol,
        "market": req.market,
        "timeframe": req.timeframe,
        "totalCandles": len(test_df),
        "totalTrades": metrics.get("total_trades", len(trades)),
        "winRate": win_rate_fraction,
        "winRatePct": win_rate_pct or 0.0,
        "netR": net_r,
        "profitFactor": _clean_num(metrics.get("profit_factor")),
        "maxDrawdownR": _clean_num(metrics.get("max_drawdown_r"), 0.0),
        "expectancyR": _clean_num(metrics.get("expectancy_r")),
    }


# Initial load at startup
load_custom_strategies()
