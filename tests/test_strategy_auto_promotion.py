from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from db.db import get_strategy_auto_promotion_policy, set_strategy_auto_promotion_policy

from web.routes.backtest import (
    _auto_promote_top_candidate,
    StrategyAutoPromotionPolicyRequest,
    get_promotion_policy,
    list_promotion_audit,
    update_promotion_policy,
)
