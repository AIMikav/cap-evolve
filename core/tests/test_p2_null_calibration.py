"""Null calibration of rule P2 on a seeded Bernoulli world (pure simulation, no adapter): the round
false-accept rate at the shipped thresholds and the K=1 group-sequential false-accept rate.

The world mimics a measured null pool: 5 always-pass tasks, 3 near-never, the rest spread over 0.1-0.9; the
pool is split F25 / C24. A candidate's true effect d raises every rollout probability by
d * w / mean(w) with w = 1 - p (the diffuse edit used in the design simulation).
"""

import math
import random

import pytest

from cap_evolve import promotion, racing  # noqa: E402

P = promotion.P2Params()
_rng = random.Random(1)
POOL = [1.0] * 5 + [0.03] * 3 + [round(0.1 + 0.8 * i / 40, 3) for i in range(41)]
_rng.shuffle(POOL)
F, C = POOL[:25], POOL[25:]
V = 0.147


def edit(p, d):
    if d == 0:
        return list(p)
    w = [(1 - x) if d > 0 else x for x in p]
    s = sum(w) / len(w)
    return [min(1.0, max(0.0, x + d * wi / s)) for x, wi in zip(p, w)]


def bern(rng, p):
    return 1.0 if rng.random() < p else 0.0


def one_round(rng, effects, *, zc, control=True, drift=0.0, het=0.0):
    K = len(effects)
    qF = [edit(F, d) for d in effects]
    qC = [[min(1.0, max(0.0, x + rng.gauss(0, het))) if het else x for x in edit(C, d + drift)]
          for d in effects]       # het: per-task effect spread (null on average, not on each task)
    phF = [sum(bern(rng, p) for _ in range(4)) / 4 for p in F]
    order = racing.draw_order(list(range(len(F))), P.rung_sizes[1], rng.random())

    def rung(j, lo, hi):
        tot, cnt = 0.0, {}
        for t in order[lo:hi]:
            tot += bern(rng, qF[j][t]) - phF[t]
            cnt[t] = cnt.get(t, 0) + 1
        return tot, cnt

    acc = {}
    for j in range(K):
        tot, cnt = rung(j, 0, P.rung_sizes[0])
        n = sum(cnt.values())
        acc[j] = (tot, cnt)
    stats = {str(j): {"mean": t / P.rung_sizes[0],
                      "z": (t / P.rung_sizes[0]) / racing.pooled_se(P.rung_sizes[0], list(c.values()), V, 4)}
             for j, (t, c) in acc.items()}
    keep = [int(x) for x in racing.halve(stats, max(1, K // 2))]
    st2 = {}
    for j in keep:
        t2, c2 = rung(j, P.rung_sizes[0], P.rung_sizes[1])
        tot = acc[j][0] + t2
        cnt = dict(acc[j][1])
        for t, c in c2.items():
            cnt[t] = cnt.get(t, 0) + c
        st2[str(j)] = {"mean": tot / P.rung_sizes[1],
                       "z": (tot / P.rung_sizes[1]) / racing.pooled_se(P.rung_sizes[1], list(cnt.values()), V, 4)}
    surv = [int(x) for x in racing.halve(st2, min(P.max_survivors, max(1, K // 4)))]
    if not surv:
        return None
    # a concurrent control drifts with the candidates; a stale one was measured before the drift
    ctrl_p = edit(C, drift) if control else list(C)
    ctrl = {str(t): [bern(rng, ctrl_p[t]) for _ in range(P.ctrl_m)] for t in range(len(C))}
    winners = []
    for j in surv:
        cand = {}
        for t in racing.draw_order(list(range(len(C))), P.conf_n, rng.random()):
            cand.setdefault(str(t), []).append(bern(rng, qC[j][int(t)]))
        r = racing.confirm_with_control(cand, ctrl, z_conf=zc, dmin=P.dmin, n_planned=len(C))
        if r["pass"]:
            winners.append((r["mean"], j))
    return max(winners)[1] if winners else None


def fwer(zc, n_rounds=3000, seed=5, **kw):
    rng = random.Random(seed)
    return sum(one_round(rng, [0.0] * 8, zc=zc, **kw) is not None for _ in range(n_rounds)) / n_rounds


@pytest.mark.parametrize("het", [0.0, 0.1, 0.2, 0.3])
def test_round_fwer_at_the_shipped_threshold_is_at_most_010_even_with_heterogeneous_effects(het):
    """Null on average, but each candidate helps some tasks and hurts others (per-task SD ``het``).
    A model-based SE that ignores this spread gave 0.113 at SD 0.1 and 0.163 at SD 0.2."""
    rate = fwer(P.z_conf, het=het)
    assert rate <= 0.10, rate
    assert rate > 0.02, "the threshold should not be vacuous"


def test_power_a_real_gain_usually_crowns_a_champion():
    rng = random.Random(9)
    wins = 0
    for _ in range(600):
        effects = [0.15] + [0.0] * 7
        wins += one_round(rng, effects, zc=P.z_conf) == 0
    assert wins / 600 > 0.45


def test_a_stale_incumbent_under_drift_inflates_false_accepts_which_is_why_the_control_exists():
    with_control = fwer(P.z_conf, n_rounds=1500, drift=0.03, control=True)
    stale = fwer(P.z_conf, n_rounds=1500, drift=0.03, control=False)
    assert with_control <= 0.10
    assert stale > 0.13 and stale > 1.8 * with_control


def test_group_sequential_false_accept_is_about_005_per_candidate_and_it_stops_early_on_losers():
    rng = random.Random(11)
    n_trials, fa, cost = 3000, 0, 0
    looks, nmax = P.gs_looks, P.gs_looks[-1]
    for _ in range(n_trials):
        ctrl = [sum(bern(rng, p) for _ in range(P.ctrl_m)) / P.ctrl_m for p in C]
        draws = racing.draw_order(list(range(len(C))), nmax, rng.random())
        tot, n, cnt = 0.0, 0, {}
        prev = 0
        verdict = "reject"
        for i, look in enumerate(looks):
            for t in draws[prev:look]:
                tot += bern(rng, C[t]) - ctrl[t]
                cnt[t] = cnt.get(t, 0) + 1
            n, prev = look, look
            se = racing.pooled_se(n, list(cnt.values()), V, P.ctrl_m)
            act = racing.gs_decide(i, n, (tot / n) / se, looks=looks, c=P.gs_c, futility=P.gs_futility)
            if act == "accept" and tot / n < P.dmin:
                act = "continue" if i < len(looks) - 1 else "kill"
            if act != "continue":
                verdict = "accept" if act == "accept" else "reject"
                break
        fa += verdict == "accept"
        cost += n
    assert fa / n_trials <= 0.06, fa / n_trials          # same per-test alpha (~0.05) as z_conf 1.6
    assert cost / n_trials < 0.85 * nmax


def test_pooled_se_matches_monte_carlo_within_5_percent():
    rng = random.Random(3)
    p = C
    m, n_tasks = 4, len(p)
    for n in (24, 96, 144):
        draws_each = racing.draw_order(list(range(n_tasks)), n, 1)
        counts = [draws_each.count(t) for t in range(n_tasks)]
        sims = []
        for _ in range(2500):
            ph = [sum(bern(rng, x) for _ in range(m)) / m for x in p]
            tot = sum(bern(rng, p[t]) - ph[t] for t in draws_each)
            sims.append(tot / n)
        mu = sum(sims) / len(sims)
        sd = math.sqrt(sum((x - mu) ** 2 for x in sims) / (len(sims) - 1))
        v = sum(x * (1 - x) for x in p) / n_tasks
        assert racing.pooled_se(n, counts, v, m) == pytest.approx(sd, rel=0.06)


@pytest.mark.parametrize("het", [0.0, 0.1, 0.2, 0.3])
def test_worst_case_two_survivors_sharing_one_control_stay_under_010(het):
    """Every round sends 2 null survivors to confirmation, both compared with the SAME control draw."""
    rng = random.Random(21)
    hits, n = 0, 3000
    for _ in range(n):
        ctrl_p = list(C)
        ctrl = {str(t): [bern(rng, ctrl_p[t]) for _ in range(P.ctrl_m)] for t in range(len(C))}
        for _j in range(2):
            q = [min(1.0, max(0.0, x + rng.gauss(0, het))) for x in C]
            cand = {}
            for t in racing.draw_order(list(range(len(C))), P.conf_n, rng.random()):
                cand.setdefault(str(t), []).append(bern(rng, q[int(t)]))
            if racing.confirm_with_control(cand, ctrl, z_conf=P.z_conf, dmin=P.dmin, n_planned=len(C))["pass"]:
                hits += 1
                break
    assert hits / n <= 0.10, hits / n
