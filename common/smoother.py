"""OU residual Kalman filter + RTS smoother around a supplied mean mu(t), with
observations injected at their EXACT times (the grid is the union of the
regular grid and the observation times) and a variance per observation.

    true(t) = mu(t) + r(t),   dr = -theta r dt + sigma dW,   obs_k = true(t_k) + N(0, var_k)

Hard anchors use a tiny variance (they are real StatsBomb locations and must be
hit); soft anchors (kickoff templates, off-camera events) a larger one.
"""
import numpy as np

HARD_VAR = 0.05 ** 2
# Chosen by held-out error in training/tune_mu_smoothing.py (sigma = 0,1,2,3,5,8 s):
# 2 s gave the lowest LOMO median error and removed >9.5 m/s steps.
MU_SMOOTH_S = 2.0


def union_grid(grid, obs_t):
    U = np.union1d(np.round(grid, 3), np.round(obs_t, 3))
    return U


def _smooth_axis(U, mu_U, obs_idx, obs_v, obs_var, theta, sigma):
    n = len(U)
    stat = sigma ** 2 / (2 * theta)
    r_pred = np.zeros(n); p_pred = np.zeros(n); r_f = np.zeros(n); p_f = np.zeros(n); phis = np.ones(n)
    obs_at = {}
    for k, v, w in zip(obs_idx, obs_v, obs_var):
        obs_at.setdefault(int(k), []).append((v, w))
    dts = np.diff(U, prepend=U[0])
    phi_all = np.exp(-theta * np.maximum(dts, 0))
    q_all = stat * (1 - phi_all ** 2)
    for k in range(n):
        if k == 0:
            r_pred[0], p_pred[0] = 0.0, stat
        else:
            phi = phi_all[k]; phis[k] = phi
            r_pred[k] = phi * r_f[k - 1]
            p_pred[k] = phi * phi * p_f[k - 1] + q_all[k]
        r, p = r_pred[k], p_pred[k]
        for v, w in obs_at.get(k, ()):
            g = p / (p + w)
            r = r + g * (v - mu_U[k] - r)
            p = (1 - g) * p
        r_f[k], p_f[k] = r, p
    r_s = r_f.copy(); p_s = p_f.copy()
    for k in range(n - 2, -1, -1):
        if p_pred[k + 1] < 1e-12:
            continue
        c = p_f[k] * phis[k + 1] / p_pred[k + 1]
        r_s[k] = r_f[k] + c * (r_s[k + 1] - r_pred[k + 1])
        p_s[k] = p_f[k] + c * c * (p_s[k + 1] - p_pred[k + 1])
    return mu_U + r_s, np.maximum(p_s, 0)


def smooth(grid, mu_x, mu_y, obs, ou):
    """grid: regular times; mu_x/mu_y on grid. obs: array (n,4) of t, x, y, var
    or (n,5) of t, x, y, var_x, var_y.
    ou: dict theta_x, sigma_x, theta_y, sigma_y. Returns U, x, y, sd on the union grid."""
    obs = np.asarray(obs, dtype=float)
    obs = obs.reshape(-1, obs.shape[-1] if obs.ndim == 2 and obs.shape[0] else 4)
    if obs.shape[1] == 4:
        obs = np.column_stack([obs, obs[:, 3]])
    obs = obs[(obs[:, 0] >= grid[0] - 1e-6) & (obs[:, 0] <= grid[-1] + 1e-6)] if len(obs) else obs
    U = union_grid(grid, obs[:, 0]) if len(obs) else np.asarray(grid, float)
    mxu, myu = np.interp(U, grid, mu_x), np.interp(U, grid, mu_y)
    oi = np.searchsorted(U, np.round(obs[:, 0], 3)) if len(obs) else np.zeros(0, int)
    x, px = _smooth_axis(U, mxu, oi, obs[:, 1], obs[:, 3], ou["theta_x"], ou["sigma_x"])
    y, py = _smooth_axis(U, myu, oi, obs[:, 2], obs[:, 4], ou["theta_y"], ou["sigma_y"])
    return U, x, y, np.sqrt(px + py)


def smooth_mean(t, v, sigma_s):
    """Zero-phase Gaussian smoothing of the model mean along a regular grid t
    (offline replay, so a centred filter is allowed). sigma_s <= 0 returns v."""
    if sigma_s <= 0 or len(v) < 3:
        return np.asarray(v, float)
    from scipy.ndimage import gaussian_filter1d
    step = float(np.median(np.diff(t)))
    return gaussian_filter1d(np.asarray(v, float), sigma_s / step, mode="nearest")
