"""Pure, deterministic implementations of IMMS runtime formulas F01-F27."""

import math

PAD_DOMAIN = {"valence": (-1.0, 1.0), "arousal": (0.0, 1.0), "dominance": (0.0, 1.0)}


def clip(x, lo, hi):
    return max(lo, min(hi, x))


def kappa(intimacy):
    return 1 + 0.6 * intimacy


def nu(nervousness):
    return 0.5 + nervousness


def tension(omega, canon, mask, exposure):
    return sum(w * abs(c - m) * e for w, c, m, e in zip(omega, canon, mask, exposure))


def relation_update(before, baseline, lam, beta, eta=1.0, kappa_v=1.0, nu_v=1.0, lo=-1.0, hi=1.0):
    return clip(baseline + lam * (before - baseline) + beta * eta * kappa_v * nu_v, lo, hi)


def pad_update(vec, tau, delta_t, impacts):
    decay = math.exp(-delta_t / tau) if tau > 0 else 0.0
    out = {}
    for key, (lo, hi) in PAD_DOMAIN.items():
        out[key] = clip(decay * vec.get(key, 0.0) + impacts.get(key, 0.0), lo, hi)
    return out


def alignment(rel_mean, goal_fit, safety, betray_cost, w1=1.0, w2=1.0, w3=1.0, w4=1.0):
    return w1 * rel_mean + w2 * goal_fit + w3 * safety - w4 * betray_cost


def faction_decision(align, current, theta_on=0.70, theta_off=0.50):
    if current is None:
        return align >= theta_on
    if current:
        return align >= theta_off
    return align >= theta_on


def evidence_weight(cred, strength, bias):
    return cred * strength * (1 - bias)


def suspicion_log_odds(ell0, evidences):
    total = ell0
    for ev in evidences:
        w = evidence_weight(ev.get("cred", 1.0), ev.get("strength", 1.0), ev.get("bias", 0.0))
        total += w * ev["log_lr"]
    return total


def sigmoid(x):
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1 + ex)


def vote_distribution(log_odds, temperature):
    if temperature <= 0:
        temperature = 1e-6
    scaled = {k: v / temperature for k, v in log_odds.items()}
    m = max(scaled.values())
    exps = {k: math.exp(v - m) for k, v in scaled.items()}
    total = sum(exps.values())
    return {k: v / total for k, v in exps.items()}


def contradiction_strength(belief_a, belief_b, strength):
    if belief_a <= 0.5 or belief_b <= 0.5:
        return 0.0
    return min(belief_a, belief_b) * strength


def leak_sigma(sigma0, arousal, stress, acting_skill, gamma1=0.5, gamma2=0.5):
    return sigma0 * (1 + gamma1 * arousal) * (1 + gamma2 * stress) * (1 - acting_skill)


def shown_attitude(true_value, mask_delta, epsilon, lo=-1.0, hi=1.0):
    return clip(true_value + mask_delta + epsilon, lo, hi)


def lie_gain(v_protect, v_frame, p_exposed, c_exposed, honesty_cost, mu):
    return v_protect + v_frame - p_exposed * c_exposed - mu * honesty_cost


def lie_heat(previous, n_challenge, n_contradiction):
    return 0.85 * previous + 0.4 * n_challenge + 0.6 * n_contradiction


def heat_target(t, total_rounds, heat_max):
    if total_rounds <= 0:
        return 0.0
    return heat_max * (max(0.0, min(1.0, t / total_rounds)) ** 1.6)


def actual_heat(n_accuse, n_contradiction, mean_arousal, alpha=1.0, beta=1.0, gamma=1.0):
    return alpha * n_accuse + beta * n_contradiction + gamma * mean_arousal


def utility(weights, fit, gain, impulse, social, risk, cost):
    return (
        weights.get("w_p", 1.0) * fit
        + weights.get("w_g", 1.0) * gain
        + weights.get("w_e", 1.0) * impulse
        + weights.get("w_s", 1.0) * social
        - weights.get("w_r", 1.0) * risk
        - weights.get("w_c", 1.0) * cost
    )


def softmax(values, temperature):
    if temperature <= 0:
        temperature = 1e-6
    scaled = {k: v / temperature for k, v in values.items()}
    m = max(scaled.values())
    exps = {k: math.exp(v - m) for k, v in scaled.items()}
    total = sum(exps.values())
    return {k: v / total for k, v in exps.items()}


def decision_temperature(t0, arousal, intoxication, gamma=0.6, gamma_prime=0.6):
    return t0 * (1 + gamma * arousal + gamma_prime * intoxication)


def memory_score(age, tau_m, importance, cosine, emo_congruence, alpha=1.0, beta=1.0, gamma=1.0, delta=1.0):
    return (
        alpha * math.exp(-age / tau_m)
        + beta * importance
        + gamma * cosine
        + delta * emo_congruence
    )


BETA_MATRIX = {
    "defend_public": {"like": 0.18, "trust": 0.22, "respect": 0.08, "suspicion": -0.10, "grudge": -0.05, "debt": 0.20},
    "share_clue": {"like": 0.12, "trust": 0.25, "respect": 0.05, "suspicion": -0.08, "grudge": 0.0, "debt": 0.15},
    "accuse_unfounded": {"like": -0.20, "trust": -0.25, "respect": -0.05, "suspicion": 0.05, "grudge": 0.22, "debt": -0.10},
    "accuse_proven": {"like": -0.10, "trust": -0.15, "respect": 0.10, "suspicion": 0.0, "grudge": 0.10, "debt": 0.0},
    "lie_exposed": {"like": -0.25, "trust": -0.45, "respect": -0.20, "suspicion": 0.30, "grudge": 0.15, "debt": 0.0},
    "caught_lying": {"like": -0.15, "trust": -0.40, "respect": -0.10, "suspicion": 0.35, "grudge": 0.05, "debt": 0.0},
    "cover_for": {"like": 0.25, "trust": 0.30, "respect": 0.0, "suspicion": -0.05, "grudge": 0.0, "debt": 0.35},
    "betray": {"like": -0.35, "trust": -0.30, "respect": -0.15, "suspicion": 0.10, "grudge": 0.40, "debt": -0.20},
    "rescue": {"like": 0.40, "trust": 0.35, "respect": 0.20, "suspicion": -0.15, "grudge": -0.20, "debt": 0.50},
    "take_credit": {"like": -0.22, "trust": -0.18, "respect": 0.05, "suspicion": 0.05, "grudge": 0.25, "debt": -0.15},
}

LOG_LR = {
    "fingerprint": 2.2,
    "eyewitness": 1.2,
    "timeline_conflict": 1.5,
    "motive": 0.5,
    "nervous": 0.3,
    "valid_alibi": -2.5,
    "guarantee": -0.8,
}
