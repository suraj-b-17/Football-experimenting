"""1D (per-axis) Kalman filter + RTS smoother for an Ornstein-Uhlenbeck
residual process around a time-varying, externally-supplied mean mu(t):

    true(t)  = mu(t) + r(t)
    dr        = -theta * r * dt + sigma * dW      (mean-reverts to 0)
    obs_k     = true(t_k) + N(0, R)                 (only at touch events)

Run on a uniform time grid; observations are injected into the update step
of whichever grid cell they fall in.
"""
import numpy as np

def ou_transition(theta, sigma, dt):
    dt = max(dt, 1e-6)
    phi = np.exp(-theta * dt)
    q = (sigma ** 2) / (2 * theta) * (1 - phi ** 2) if theta > 1e-9 else sigma ** 2 * dt
    return phi, q

def smooth_axis(grid_t, obs_t, obs_v, theta, sigma, obs_r, stationary_var):
    """Returns (mean, var) arrays over grid_t for the OU residual r(t),
    having assimilated observations (obs_t, obs_v) with variance obs_r."""
    n = len(grid_t)
    # map each observation to the nearest grid index
    obs_by_idx = {}
    for ot, ov in zip(obs_t, obs_v):
        idx = int(np.argmin(np.abs(grid_t - ot)))
        obs_by_idx.setdefault(idx, []).append(ov)

    r_pred = np.zeros(n); p_pred = np.zeros(n)
    r_filt = np.zeros(n); p_filt = np.zeros(n)
    phis = np.zeros(n)

    # init at first grid point: stationary prior (no info yet)
    r_pred[0] = 0.0
    p_pred[0] = stationary_var
    for k in range(n):
        if k > 0:
            dt = grid_t[k] - grid_t[k - 1]
            phi, q = ou_transition(theta, sigma, dt)
            phis[k] = phi
            r_pred[k] = phi * r_filt[k - 1]
            p_pred[k] = phi ** 2 * p_filt[k - 1] + q
        r_filt[k], p_filt[k] = r_pred[k], p_pred[k]
        if k in obs_by_idx:
            for ov in obs_by_idx[k]:
                kk = p_filt[k] / (p_filt[k] + obs_r)
                r_filt[k] = r_filt[k] + kk * (ov - r_filt[k])
                p_filt[k] = (1 - kk) * p_filt[k]

    # RTS backward smoother
    r_smooth = r_filt.copy(); p_smooth = p_filt.copy()
    for k in range(n - 2, -1, -1):
        phi = phis[k + 1]
        if p_pred[k + 1] < 1e-9:
            continue
        c = p_filt[k] * phi / p_pred[k + 1]
        r_smooth[k] = r_filt[k] + c * (r_smooth[k + 1] - r_pred[k + 1])
        p_smooth[k] = p_filt[k] + c ** 2 * (p_smooth[k + 1] - p_pred[k + 1])
    return r_smooth, p_smooth

def smooth_track(grid_t, mu_x, mu_y, obs_t, obs_x, obs_y,
                  theta_x, sigma_x, theta_y, sigma_y, obs_r=2.25):
    stat_x = sigma_x ** 2 / (2 * theta_x)
    stat_y = sigma_y ** 2 / (2 * theta_y)
    mu_x_at_obs = np.interp(obs_t, grid_t, mu_x)
    mu_y_at_obs = np.interp(obs_t, grid_t, mu_y)
    rx, px = smooth_axis(grid_t, obs_t, np.array(obs_x) - mu_x_at_obs, theta_x, sigma_x, obs_r, stat_x)
    ry, py = smooth_axis(grid_t, obs_t, np.array(obs_y) - mu_y_at_obs, theta_y, sigma_y, obs_r, stat_y)
    return mu_x + rx, mu_y + ry, px, py
