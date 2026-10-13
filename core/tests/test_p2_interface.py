"""Rule P2: parameters, pure statistics (racing.py) and the evaluator's public interface."""

import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "core"))

from cap_evolve import evaluator, promotion, racing  # noqa: E402


def test_p2_is_opt_in_and_rule_p_is_untouched():
    assert promotion.P2Params.from_spec({}) is None
    assert promotion.P2Params.from_spec({"promotion_rule": "P"}) is None
    assert promotion.PromotionParams.from_spec({"promotion_rule": "P2"}) is None
    assert promotion.PromotionParams.from_spec({"promotion_rule": "P"}) == promotion.PromotionParams()
    p = promotion.P2Params.from_spec({"promotion_rule": "P2", "p2_conf_n": 98})
    assert p.conf_n == 98 and p.z_conf == 1.7 and p.rung_sizes == (12, 36) and p.ctrl_m == 4
    with pytest.raises(ValueError):
        promotion.P2Params.from_spec({"promotion_rule": "P2", "p2_rung_sizes": [40, 12]})


def test_worst_case_matches_the_design_note():
    p = promotion.P2Params()
    assert p.worst_case_cost(8, 24) == 582          # 96 + 96 + 96 + 2 * 147
    assert p.k_for_budget(550) == 6 and p.k_for_budget(2000) == 8 and p.k_for_budget(0) == 8


def test_halve_is_kill_only_and_requires_positive_mean():
    stats = {"a": {"mean": 0.1, "z": 1.0}, "b": {"mean": 0.2, "z": 2.0},
             "c": {"mean": -0.1, "z": 3.0}, "d": {"mean": 0.0, "z": 9.0}}
    assert racing.halve(stats, 2) == ["b", "a"]
    assert racing.halve({"x": {"mean": -1.0, "z": 0.0}}, 3) == []


def test_pooled_se_grows_with_repeated_passes():
    one_pass = racing.pooled_se(24, [1] * 24, 0.147, 4)
    six_pass = racing.pooled_se(144, [6] * 24, 0.147, 4)
    assert six_pass < one_pass
    assert six_pass > math.sqrt(0.147 / 144)       # the incumbent term is not averaged away


def test_group_sequential_boundary_and_futility():
    assert racing.gs_decide(0, 12, -0.6) == "kill"
    assert racing.gs_decide(0, 12, 0.0) == "continue"
    assert racing.gs_decide(4, 147, 1.9) == "kill"
    assert racing.gs_decide(4, 147, 2.1) == "accept"
    assert racing.gs_decide(1, 24, 99.0) == "accept"


def test_confirmation_verdict_bit_hides_everything_but_the_verdict():
    assert evaluator.ConfirmVerdict("c", "accept", 0.1, 3.0, 147).bit() == "accept"
    assert evaluator.ConfirmVerdict("c", "unconfirmed").bit() == "pending"
    assert set(evaluator.STATUSES) >= {"unconfirmed", "unfunded"}
