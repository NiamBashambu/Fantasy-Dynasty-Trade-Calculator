# Data Science in the Dynasty Trade Analyzer

A plain-English and mathematical walkthrough of every model and statistical process in this repo.

- Offline pipeline: `datascience/` (collect, features, model, backtest, run_all, figures)
- Runtime consumers: `dynasty/insights.py`, `dynasty/trades.py`, `dynasty/needs.py`, `dynasty/values.py`
- Artifacts the pipeline writes (the web app reads these; it never imports pandas or scikit-learn): `dynasty/data/aging_curves.json`, `model_edges.json`, `calibration.json`, `value_history.json`

Numbers quoted as "results" come from the README's last run (October 2026). They are not recomputed here.

---

## Table of contents

1. [Big picture](#1-big-picture)
2. [Data collection](#2-data-collection)
3. [Feature engineering](#3-feature-engineering)
4. [Aging curves](#4-aging-curves)
5. [Production model (next-season points)](#5-production-model-next-season-points)
6. [Evaluation: walk-forward validation](#6-evaluation-walk-forward-validation)
7. [Market vs. fundamentals (the edge model)](#7-market-vs-fundamentals-the-edge-model)
8. [Bench value estimation](#8-bench-value-estimation)
9. [Calibrating trade fairness on real trades](#9-calibrating-trade-fairness-on-real-trades)
10. [Position adjustments (ridge regression)](#10-position-adjustments-ridge-regression)
11. [TE premium](#11-te-premium)
12. [Forward test](#12-forward-test)
13. [Roster needs and VORP](#13-roster-needs-and-vorp)
14. [How it all feeds trade scoring at runtime](#14-how-it-all-feeds-trade-scoring-at-runtime)
15. [Results, limitations, and honest caveats](#15-results-limitations-and-honest-caveats)
16. [Glossary](#16-glossary)

---

## 1. Big picture

The market (FantasyCalc) already gives every player and pick a trade value. The data science layer answers four questions the market doesn't:

| Question | Method | Output |
|---|---|---|
| How do players age? | Empirical year-over-year retention | `aging_curves.json` |
| How many points will a player score next year? | Gradient-boosted regression with quantile bands | used internally for edges |
| Is the market over- or under-pricing a player relative to fundamentals? | Out-of-fold gradient boosting; the residual is the "edge" | `model_edges.json` |
| How much should a trade discount "two mid assets for one star"? | Grid search and bootstrap on real accepted trades | `calibration.json` |

Pipeline flow (`run_all.py`):

```
collect (snapshots, trades, stats, ids)
   -> season_features()         player-season table + next_points label
   -> aging_curves()            retention by position and age
   -> walk_forward()            honest evaluation vs. baselines
   -> fit_production_model()    mean + 20th + 80th percentile models
   -> build_edges()             market value vs. fundamentals, per QB format
   -> calibrate()               depth decay, even threshold, position multipliers, forward test
   -> te_premium_lift()
   -> write JSON artifacts (+ figures)
```

---

## 2. Data collection

Code: `datascience/collect.py`.

| Source | Endpoint | Stored as | Purpose |
|---|---|---|---|
| FantasyCalc values | `/values/current` | One CSV per day per format (`{date}_q{qbs}_p{ppr}.csv`) | Market values and their history |
| FantasyCalc trades | `/trades` | JSONL, de-duplicated by trade id | Real accepted trades for calibration |
| nflverse | `stats_player_reg_{season}.csv` (2012 onward) | `stats_{season}.csv` | Season stats for features |
| DynastyProcess | `db_playerids.csv` | `player_ids.csv` | Maps Sleeper id to nflverse (`gsis`) id, plus birthdate and draft info |

Formats collected: `numQbs in {1, 2}` and `ppr in {0, 0.5, 1.0}`, which is 6 formats. Each is fetched with `numTeams=12`.

Design notes:
- The trades endpoint returns only the latest ~50 per query, so trades accumulate only if the collector runs regularly. The code appends and de-duplicates by id.
- Old seasons are cached and never re-downloaded (they don't change). Only the last two seasons are refreshed.
- HTTP requests retry 3 times with linearly increasing backoff (1.5 s, 3 s, 4.5 s).
- Snapshots must be collected over time (for example by a daily cron job) to build value history and enable the forward test (section 12).

---

## 3. Feature engineering

Code: `datascience/features.py`, function `season_features`.

### 3.1 Unit of analysis

One row is one player-season for positions QB, RB, WR, TE in the regular season. The row describes the player at the end of season $t$ and carries a label for season $t+1$.

### 3.2 Features

For player $i$ in season $t$, with $g$ games played:

| Feature | Definition |
|---|---|
| `points` | PPR fantasy points in season $t$ |
| `ppg` | $\text{points} / \max(g, 1)$ |
| `ppg_prev`, `ppg_prev2` | ppg in $t-1$ and $t-2$ (via a lagged merge; missing if the player didn't play) |
| `points_prev` | points in $t-1$ |
| `ppg_change` | $\text{ppg}_t - \text{ppg}_{t-1}$ |
| `age` | $(\text{Sept 1 of } t - \text{birthdate})/365.25$, in years |
| `years_exp` | $t - \text{draft year}$ |
| `draft_ovr` | Overall draft pick number (draft capital; lower is better; NaN if undrafted) |
| `games` | Games played in $t$ |
| `targets_pg`, `carries_pg`, `attempts_pg` | Per-game usage: targets, carries, pass attempts divided by $\max(g, 1)$ |
| `target_share`, `wopr`, ... | Opportunity shares from nflverse |
| `pos_QB`, `pos_RB`, `pos_WR`, `pos_TE` | One-hot position indicators |

The full feature list is `FEATURE_COLUMNS`. Missing values are left as NaN. The model used (histogram gradient boosting) handles NaN natively.

### 3.3 Label

$$
y_{i,t} = \text{next\_points}_{i,t} = \begin{cases} \text{points}_{i,t+1} & \text{if } t < t_{\text{last}} \text{ (the player appears in } t+1) \\ 0 & \text{if } t < t_{\text{last}} \text{ and the player does not appear} \\ \text{NaN} & t = t_{\text{last}} \end{cases}
$$

A player who disappears (retired, cut, injured all year) gets a label of 0 rather than being dropped. This avoids survivorship bias: the model has to learn that some players fall off completely.

### 3.4 Preventing leakage

- `latest_complete_season` returns the latest season in which someone has played at least 16 games (`MIN_COMPLETE_GAMES`). Incomplete seasons are dropped entirely, so a half-finished season can never become a label.
- `training_frame` keeps only rows with `games >= 4` and a known label.
- Evaluation uses walk-forward splits (section 6), so the future never trains the model.

---

## 4. Aging curves

Code: `features.aging_curves`. Consumed at runtime by `insights.retention` and `insights.longevity`.

Not machine learning, just careful aggregation.

### 4.1 Retention definition

Take pairs of consecutive seasons for the same player where the player played at least 8 games in both (`min_games=8`, and the next-season games are approximated as $\text{next\_points}/\text{next\_ppg} \geq 7.5$). Bucket each pair by age $a = \text{round}(\text{age})$, clipped to $[21, 36]$.

For position $p$ and age bucket $a$:

$$
R_p(a) = \frac{\sum_{i \in S_{p,a}} \text{ppg}_{i,t+1}}{\sum_{i \in S_{p,a}} \text{ppg}_{i,t}}
$$

where $S_{p,a}$ is the set of qualifying players. A value of $R = 0.85$ means players of that position and age keep about 85% of their points per game next season.

Why a ratio of sums and not a mean of ratios? Averaging individual ratios $\text{ppg}_{t+1}/\text{ppg}_t$ lets a low-volume player (for example $0.5 \to 2.0$ ppg gives ratio 4.0) dominate. The ratio of sums weights each player by his production, which is more stable.

### 4.2 Smoothing

Individual age buckets are noisy (few 35-year-old QBs). The numerator and denominator are each convolved with the kernel $[1, 2, 1]$ before dividing:

$$
\tilde{N}(a) = N(a-1) + 2N(a) + N(a+1), \qquad \tilde{D}(a) = D(a-1) + 2D(a) + D(a+1), \qquad R_p(a) = \frac{\tilde{N}(a)}{\tilde{D}(a)}
$$

Ages with no data are filled forward and then backward (`ffill`, then `bfill`).

### 4.3 Longevity (runtime)

`insights.longevity` computes expected production over the next 3 seasons relative to one season, for an asset of position $p$ and age $a$:

$$
L(p, a) = \sum_{k=0}^{2} \prod_{j=0}^{k-1} R_p(a + j) = 1 + R_p(a) + R_p(a)\,R_p(a+1)
$$

Example: if $R(26)=0.97$ and $R(27)=0.93$, then $L = 1 + 0.97 + 0.97 \times 0.93 \approx 2.87$. Picks use the age of a rookie (21).

If the artifact is missing, the fallback is $R = \max(0.6,\ 1 - 0.03\,\max(0, a - 25))$.

---

## 5. Production model (next-season points)

Code: `datascience/model.py`, `fit_production_model` and `ProductionModel`.

### 5.1 Task

Supervised regression: learn $f(\mathbf{x}_{i,t}) \approx y_{i,t}$, next season's PPR points.

### 5.2 Algorithm: histogram gradient boosting

`sklearn.ensemble.HistGradientBoostingRegressor`. Boosting builds an additive model of small trees, each fit to the errors of the ensemble so far:

$$
F_0(\mathbf{x}) = \text{constant}, \qquad F_m(\mathbf{x}) = F_{m-1}(\mathbf{x}) + \eta\, h_m(\mathbf{x}), \qquad m = 1, \dots, M
$$

where $h_m$ is a shallow regression tree fit to the negative gradient of the loss at $F_{m-1}$, and $\eta$ is the learning rate. For squared error, the negative gradient is the residual $y - F_{m-1}(\mathbf{x})$.

"Histogram" means features are binned into at most 255 bins, which makes split finding fast.

Hyperparameters (`_gbm`):

| Parameter | Value | Why |
|---|---|---|
| `max_depth` | 3 | Shallow trees: fewer interactions, less overfitting |
| `learning_rate` ($\eta$) | 0.05 | Small steps, more robust |
| `max_iter` ($M$) | 250 | 250 trees |
| `min_samples_leaf` | 25 | Each leaf must contain at least 25 player-seasons |
| `l2_regularization` | 1.0 | Penalizes large leaf values |
| `random_state` | 7 | Reproducibility |

### 5.3 Three models: a point estimate and an uncertainty band

Three separate models are fit on the same features, differing only in loss:

1. **Mean model**: squared-error loss $L(y, \hat{y}) = (y - \hat{y})^2$.
2. **Low model**: quantile loss with $\tau = 0.2$.
3. **High model**: quantile loss with $\tau = 0.8$.

The quantile (pinball) loss for quantile $\tau$:

$$
\rho_\tau(y, \hat{y}) = \begin{cases} \tau\,(y - \hat{y}) & y \geq \hat{y} \\ (1-\tau)\,(\hat{y} - y) & y < \hat{y} \end{cases}
$$

Minimizing it makes $\hat{y}$ estimate the $\tau$-th conditional quantile. With $\tau=0.2$, underpredicting is penalized less than overpredicting, so the model settles where about 20% of outcomes fall below it.

### 5.4 Post-processing (`ProductionModel.predict`)

$$
\hat{y} = \max(0, F_{\text{mean}}), \quad \hat{y}_{\text{lo}} = \min\big(\max(0, F_{\text{low}}),\ \hat{y}\big), \quad \hat{y}_{\text{hi}} = \max\big(\max(0, F_{\text{high}}),\ \hat{y}\big)
$$

Points can't be negative, and the band is forced to contain the point estimate. (Separately trained quantile models can cross.) The band $[\hat{y}_{\text{lo}}, \hat{y}_{\text{hi}}]$ is nominally an 80%−20% = 60% prediction interval.

---

## 6. Evaluation: walk-forward validation

Code: `model.walk_forward`, `_metrics`, `summarize_walk_forward`.

### 6.1 Why not a random train/test split

Random splits would let the model train on 2023 data and be tested on 2020. That is not how it will be used. Walk-forward mimics deployment.

### 6.2 Procedure

For each test season $s$ from the midpoint of the history onward:

1. Train on all rows with `season < s`. Skip the fold if there are fewer than 200 training rows.
2. Predict rows with `season == s`.
3. Score the predictions against the real next-season points.

The last test season is $t_{\text{last}} - 1$, since $t_{\text{last}}$ has no labels. The README reports 7 folds.

### 6.3 Baselines

| Baseline | Prediction |
|---|---|
| Persistence | $\hat{y} = \text{points}_t$ (next year equals this year) |
| Age curve | $\hat{y} = \text{points}_t \times R_p(\text{round}(\text{age}))$, with age clipped to $[21, 36]$ and 26 if unknown |

A model that can't beat these isn't worth shipping.

### 6.4 Metrics

For $n$ test players with errors $e_i = \hat{y}_i - y_i$:

$$
\text{MAE} = \frac{1}{n}\sum_i |e_i|, \qquad \text{RMSE} = \sqrt{\frac{1}{n}\sum_i e_i^2}
$$

$$
\text{Spearman} = \frac{1}{|P|}\sum_{p \in P} \rho_s\big(\hat{y}_p, y_p\big)
$$

Spearman's $\rho_s$ is the correlation of the rankings. It is computed within each position (a position is skipped if it has 10 or fewer players or constant predictions) and then averaged over positions. This tests the question that matters in fantasy: does the model order players of the same position correctly?

$$
\text{Band coverage} = \frac{1}{n}\sum_i \mathbb{1}\big[\hat{y}_{\text{lo},i} \leq y_i \leq \hat{y}_{\text{hi},i}\big]
$$

If the 20/80 quantiles were perfectly calibrated, coverage would be 60%.

### 6.5 Results (README)

| Method | MAE | Spearman |
|---|---|---|
| Model | about 42 | 0.71 |
| Persistence | about 47 | 0.68 |
| Age curve | about 46 | n/a |

Band coverage was about 77%, above the 60% target. The bands are conservative (too wide).

Interpretation: the gain is real but modest, because year-to-year fantasy output is dominated by injuries, role changes and luck.

---

## 7. Market vs. fundamentals (the edge model)

Code: `model.build_edges`, `fit_value_model`, `out_of_fold_log_value`. Runtime: `insights.edges`, `insights.edge_fraction`, `insights.lists`.

### 7.1 Idea

Learn how the market prices a player from fundamentals alone. When the actual price differs from that learned price, that gap is a **relative-value signal**: the player costs more or less than similar profiles. It is not a claim the market is wrong, because the model can't see injuries, depth charts, or coaching.

### 7.2 Step 1: project everyone

For the latest complete season, `ProductionModel.predict` produces $\hat{y}$ (`pred_points`) and the band for each player.

### 7.3 Step 2: join with the market

An inner join of the latest FantasyCalc snapshot (players with value > 0 at QB/RB/WR/TE) with projections on `sleeper_id`. The market's current age replaces the season-start age when available.

Players used to fit the model must have `value >= 300` (`min_value`) and a known age. The code raises an error if fewer than 60 remain.

### 7.4 Step 3: model log market value

Features (`VALUE_FEATURES`): `pred_points`, `points`, `age`, `years_exp`, `draft_ovr`, and the four position dummies.

Target: $z_i = \ln(\text{value}_i)$. The log is used because market values are right-skewed and prices behave multiplicatively: a star is a multiple of a role player, not a fixed number of points above. Errors in log space become percentage errors.

The model is a gradient booster with `max_depth=3`, `learning_rate=0.06`, `max_iter=200`, `min_samples_leaf=8`, `l2_regularization=1`.

### 7.5 Step 4: out-of-fold (OOF) predictions

If the model were trained on all players and then asked about each of them, it could partly memorize each player's price, and the residual would shrink toward zero. So, with 5-fold `KFold` (shuffled, seed 7):

$$
\hat{z}_i^{\text{OOF}} = f^{(-k(i))}(\mathbf{x}_i)
$$

where $f^{(-k(i))}$ is trained on the 4 folds that don't contain player $i$. Every player is priced by a model that never saw him.

### 7.6 Step 5: residual, z-score, signal

$$
r_i = z_i - \hat{z}_i^{\text{OOF}} = \ln\frac{\text{market}_i}{\text{fundamental}_i}, \qquad \sigma = \text{std}(r)
$$

$$
\text{fundamental}_i = e^{\hat{z}_i^{\text{OOF}}}, \qquad \text{edge}_i = \text{fundamental}_i - \text{market}_i, \qquad \text{edge\%}_i = \frac{\text{edge}_i}{\text{market}_i} \times 100
$$

$$
\text{signal score } s_i = -\frac{r_i}{\sigma}
$$

The sign is flipped so that a positive score means the market is cheaper than fundamentals imply.

$$
\text{signal}_i = \begin{cases} \textbf{buy} & s_i \geq 1.25 \\ \textbf{sell} & s_i \leq -1.25 \\ \textbf{hold} & \text{otherwise} \end{cases} \qquad (\texttt{SIGNAL\_Z} = 1.25)
$$

If the residuals were roughly normal, $|s| \geq 1.25$ would flag only the most extreme ~21% (about 10.5% each side). The threshold is intentionally strict.

### 7.7 Fit quality

$$
R^2_{\text{OOF}} = 1 - \frac{\sum_i r_i^2}{\sum_i \big(z_i - \bar{z}\big)^2}
$$

The README reports about 0.5: half the variance in log market value is explained by fundamentals out of sample. The other half (hype, injuries, situation, noise) is what the model can't see, which is why edges are noisy.

### 7.8 Runtime use of the edge

`insights.edge_fraction` returns $\text{edge\%}/100$, clipped to $[-0.5, 0.5]$ so one noisy player can't dominate. Picks and unmodeled players (such as rookies without NFL history) get 0. The `lists` helper shows top buy/sell players with market value of at least 1500 (sorted by $s$).

---

## 8. Bench value estimation

Code: end of `build_edges`.

Many rostered players have no market value. For fair roster math, the app estimates one.

1. Fit the value model on all priced players (`fit_value_model`, no folds).
2. Candidate pool: players not priced by the market, with a Sleeper id, `pred_points > 20`, and a known age. Experience is incremented by 1 (they are being valued going into next year), and age is shown as age + 1.
3. Estimate $\hat{v} = \exp(F_{\text{value}}(\mathbf{x}))$.
4. Cap so a bench guess never looks like a real asset:

$$
\text{cap} = P_{10}(\text{market values}), \qquad v_{\text{bench}} = \max\big(10,\ \min(\hat{v},\ 0.9\ \text{cap})\big)
$$

---

## 9. Calibrating trade fairness on real trades

Code: `datascience/backtest.py`. Runtime constants: `dynasty/trades.py` (`DEPTH_DECAY`, `EVEN_THRESHOLD`).

### 9.1 The fairness function

Values aren't simply additive. Two mid-tier assets are rarely worth one star because only so many players start and roster spots are limited. So each side's assets are sorted by value descending and the $i$-th best (0-indexed) is discounted by $\delta^i$:

$$
T(\text{side};\ \delta) = \sum_{i=0}^{n-1} v_{(i)}\ \delta^{\,i}, \qquad v_{(0)} \geq v_{(1)} \geq \dots
$$

Fairness (0 to 100):

$$
\text{fairness} = 100 \cdot \frac{\min(T_A, T_B)}{\max(T_A, T_B)}
$$

The side that receives the larger package wins. The verdict is "even" if fairness is at least the even threshold.

Example with $\delta = 0.65$: one asset worth 8000 vs. two assets 5000 and 4000: $T_A = 8000$, $T_B = 5000 + 4000(0.65) = 7600$, fairness = 95. With no discount ($\delta=1$) the two-for-one side would total 9000 and win.

### 9.2 The premise for calibration

Every trade in the FantasyCalc feed was accepted by two managers. On average, a good fairness function should see real trades as approximately balanced. So choose $\delta$ to make real trades look as balanced as possible.

Caveats (repeated in section 15): accepted does not mean fair, trades are valued at today's market values, and the sample size is whatever has been collected.

### 9.3 Building the trade table

`trade_table` attaches market values to each trade from the latest snapshot for the matching format (`num_qbs`, and PPR rounded to the nearest of 0, 0.5, 1.0). Trades with any asset that isn't priced (or with an empty side) are dropped.

### 9.4 Loss function

For trade $j$ with totals $T_{A,j}(\delta)$, $T_{B,j}(\delta)$:

$$
\ell_j(\delta) = \left[\ln\frac{\max(T_{A,j}, 1)}{\max(T_{B,j}, 1)}\right]^2, \qquad \mathcal{L}(\delta) = \frac{1}{N}\sum_{j=1}^{N} \ell_j(\delta)
$$

The log-ratio is symmetric (swapping A and B only flips its sign, and squaring removes it), and is zero for a perfectly balanced trade. It penalizes proportional imbalance, so a 6000-vs-4000 trade is as unbalanced as 3000-vs-2000.

### 9.5 Grid search

$$
\hat{\delta} = \arg\min_{\delta \in \{0.500, 0.505, \dots, 1.000\}} \mathcal{L}(\delta)
$$

The loss for every (trade, $\delta$) pair is precomputed into an $N \times 101$ matrix, so the search and bootstrap are fast. If the best value sits on the lower edge (0.5), it is flagged, because the true optimum may lie outside the search range.

### 9.6 Bootstrap confidence interval

Resample $N$ trades with replacement $B = 300$ times. For each resample, recompute $\hat{\delta}^{*}_b = \arg\min_\delta \mathcal{L}^{*}_b(\delta)$. The 90% interval is the 5th and 95th percentiles of $\{\hat{\delta}^{*}_b\}$.

The bootstrap shows how sensitive the answer is to which trades happened to be in the sample. A narrow interval means a stable estimate.

### 9.7 Even threshold

Compute fairness for every real trade at $\hat{\delta}$ and take the median:

$$
\text{even\_threshold} = \text{clip}\Big(\text{round}\big(\text{median}_j\ \text{fairness}_j\big),\ 80,\ 95\Big)
$$

Real accepted trades are usually not perfectly even (managers differ in preferences), so "even" is defined relative to what actual trades look like. The README reports about 82. The pipeline also stores the 10/25/50/75/90th fairness percentiles.

### 9.8 Safeguards

- If fewer than 40 trades are usable (`MIN_TRADES`), the pipeline keeps the old constants ($\delta=0.92$, even threshold 90) and records a status message. This avoids fitting noise.
- At runtime, `insights` only trusts the artifact when `status == "ok"`, and clamps $\delta \in [0.5, 1]$ and the threshold to $[80, 95]$. A missing artifact degrades to defaults.

### 9.9 Results (README)

On 280 real trades, $\hat{\delta} \approx 0.65$ with 90% interval about 0.58 to 0.71. The old hand-picked 0.92 had higher loss than 0.65 but lower than no discount at all ($\delta = 1$). The loss curve is flat between about 0.6 and 0.7, so any value in that range is about equally good.

---

## 10. Position adjustments (ridge regression)

Code: `backtest.fit_position_adjustments`. Applied at runtime in `values.py` through `insights.position_multipliers`.

### 10.1 Question

After accounting for the depth discount, do real trades systematically pay more or less for some position than the market value implies (for example, are QBs consistently overpriced relative to what trades clear at)?

### 10.2 Design matrix

For each trade $j$, with side-total $T$ and raw-value sum $S$ for each side:

$$
y_j = \ln\frac{T_{A,j}}{T_{B,j}}, \qquad x_{j,p} = \frac{V_{A,j,p}}{S_{A,j}} - \frac{V_{B,j,p}}{S_{B,j}}, \quad p \in \{\text{QB, RB, WR, TE}\}
$$

where $V_{A,j,p}$ is the sum of values of position $p$ assets on side A. In words, $x_{j,p}$ is how much more of side A's package (as a share of value) is position $p$ than side B's.

### 10.3 Symmetrization

Which side is called "A" is arbitrary, so every trade is added twice: $(\mathbf{x}, y)$ and $(-\mathbf{x}, -y)$. This forces the fit to be label-invariant (no intercept is needed, and none is used).

### 10.4 Ridge estimate

$$
\hat{\boldsymbol{\beta}} = \big(X^\top X + \lambda I\big)^{-1} X^\top \mathbf{y}, \qquad \lambda = 5
$$

The ridge penalty shrinks coefficients toward zero. It also keeps the system solvable, because the position shares are strongly related to one another.

Interpretation: a positive $\beta_p$ means the side that is heavier in position $p$ tends to have the larger total, which is to say position $p$ is **overpriced** by the market relative to where trades clear.

### 10.5 Bootstrap gating

Resample trades (both mirrored copies together) 300 times, refit, and take the 5th and 95th percentile of each $\beta_p$.

A coefficient is applied only if its 90% interval excludes zero:

$$
\text{significant}_p = \big[\ \text{lo}_p > 0\ \big] \vee \big[\ \text{hi}_p < 0\ \big]
$$

### 10.6 Multiplier

For significant positions:

$$
m_p = \text{clip}\Big(\exp\big(-0.5\,\hat\beta_p\big),\ 0.90,\ 1.10\Big)
$$

The 0.5 factor halves the estimated effect (a conservative shrink), and the clip caps the adjustment at plus or minus 10%. Positions that aren't significant get no adjustment. At runtime, `insights.position_multipliers` clamps again to $[0.9, 1.1]$.

---

## 11. TE premium

Code: `features.te_premium_lift`, `insights.te_premium_multiplier`.

If a league pays an extra bonus $b$ per reception to tight ends, a TE's score rises by about $b$ times his receptions. Relative to his points, the "lift" is:

$$
\text{lift} = \frac{\sum_{\text{top-12 TEs per season}} \text{receptions}}{\sum_{\text{top-12 TEs per season}} \text{points}}
$$

computed over the last 5 seasons, using TEs with at least 8 games, ranked by points within each season. Then a TE's relative value multiplier is:

$$
m_{\text{TE}}(b) = 1 + \min(b,\ 1.5)\cdot \text{lift}
$$

Example: with lift of 0.35 and $b = 0.5$, TEs are worth about $1 + 0.175 = 1.175$ times as much. Other positions are unaffected. The bonus is capped at 1.5, and the multiplier is 1 when there is no bonus or no artifact.

---

## 12. Forward test

Code: `backtest.forward_test`.

### 12.1 Question

When the fairness heuristic says one side won a trade, did that side actually gain more market value afterward? This is a genuine out-of-sample test, unlike the in-sample calibration.

### 12.2 Procedure

For each trade with a date before the latest snapshot:

1. Look up each asset's value at the snapshot at or before the trade date (`early`) and at the latest snapshot (`late`).
2. The heuristic's winner is the side receiving the larger discounted total at trade time. Side 1 receives side 2's assets, so compare $T(\text{side 2 assets})$ to $T(\text{side 1 assets})$.
3. The actual winner is the side whose received assets gained more raw value: $\Delta_k = \sum (\text{late} - \text{early})$ over the assets that side received.
4. Record a hit if the two winners agree.

### 12.3 Statistic

$$
\text{winner\_hit\_rate} = \frac{\#\text{hits}}{n}
$$

A rate near 0.5 means the heuristic has no predictive power. A rate well above 0.5 means it identifies value that persists.

### 12.4 Activation conditions

It reports a status instead of a number unless the snapshots span at least 7 days (`MIN_FORWARD_DAYS`) and there are at least 20 comparable trades. This is why running the snapshot collector daily matters.

---

## 13. Roster needs and VORP

Code: `dynasty/needs.py`. This is deterministic optimization and bookkeeping, not statistical learning, but it supplies model inputs.

### 13.1 Starting lineups (greedy fill)

Slots come from the league's `roster_positions` (QB, RB, WR, TE, FLEX, SUPER_FLEX, and so on). Each slot has a set of eligible positions, for example `FLEX = {RB, WR, TE}`.

`fill_lineup` fills narrow slots first (QB, RB, WR, TE, then WRRB_FLEX, REC_FLEX, FLEX, SUPER_FLEX). Each slot takes the highest-valued unused eligible player. Filling narrow slots first stops a flexible slot from stealing a player a position-specific slot needed.

### 13.2 League-wide baselines (per position $p$)

- **Starter bar:** $B_p = \text{median}\{v : \text{player is a starter on any team at } p\}$. A "good starter" level.
- **Replacement level:** $\text{Rep}_p = \max\{v : \text{player at } p \text{ is not a starter on any team}\}$. The best player any team could acquire for free (a free agent or a bench player that no one starts).

### 13.3 Team metrics

**VORP** (value over replacement), a standard cross-position comparison:

$$
\text{VORP} = \sum_{\text{starters } s}\ \max\big(0,\ v_s - \text{Rep}_{p(s)}\big)
$$

**Need** at position $p$: how far the team's starters at $p$ fall below the bar, summed over its starters:

$$
\text{need}_p = \sum_{s \text{ at } p} \max\Big(0,\ \frac{B_p - v_s}{B_p}\Big)
$$

An empty slot is counted as unfilled demand, spread equally over eligible positions: for each empty slot with $k$ eligible positions, add $1/k$ to each eligible position's need.

**Surplus** (tradeable bench value, in "starter units"):

$$
\text{surplus}_p = \sum_{\text{bench } b \text{ at } p,\ v_b > \text{Rep}_p} \frac{v_b - \text{Rep}_p}{B_p}
$$

### 13.4 Asset quality

$$
q(a) = \min\Big(1,\ \frac{v_a}{B_{p(a)}}\Big) \quad (0 \text{ for picks})
$$

How much of a starter the asset is, from 0 to 1.

### 13.5 Trade fit

For a trade where I give $G$ and receive $R$ with partner $P$:

$$
\text{fit}_{\text{me}} = \sum_{a \in R} \text{need}^{\text{me}}_{p(a)}\ q(a) - \sum_{a \in G} \text{need}^{\text{me}}_{p(a)}\ q(a)
$$

$$
\text{fit}_{\text{partner}} = \sum_{a \in G} \text{need}^{P}_{p(a)}\ q(a) - \sum_{a \in R} \text{need}^{P}_{p(a)}\ q(a)
$$

Positive means the trade moves a team's quality toward its areas of need. The partner term matters because a trade only happens if the other manager has a reason to accept.

### 13.6 Partner ranking

$$
\text{match}(P) = \sum_{p}\Big(\text{need}^{\text{me}}_p\ \text{surplus}^{P}_p + \text{need}^{P}_p\ \text{surplus}^{\text{me}}_p\Big)
$$

Teams whose surplus matches my needs and whose needs match my surplus rank highest.

---

## 14. How it all feeds trade scoring at runtime

Code: `dynasty/trades.py`.

### 14.1 Search

For each partner and each of my packages:

- My pool is my 12 most valuable assets with value at least 800 (`MIN_TRADE_ASSET_VALUE`, `SEARCH_POOL_SIZE`). Packages are all 1- and 2-asset subsets. The same applies to the partner. The 2-for-2 case is skipped.
- Keep a candidate only if fairness is at least `min_fairness = round(100 (1 - tolerance))`, where tolerance is 6%, 10% or 15% for low, medium or high risk.

### 14.2 Score

The base score is

$$
\text{score} = 0.5 \cdot \frac{\text{fairness}}{100} + \text{(strategy term)} + \text{(needs term)} + \text{(model term)} + \text{(fit term)}
$$

Auxiliary quantities:

- Win-now change: $\Delta_{\text{now}} = \dfrac{\sum_R \text{redraft} - \sum_G \text{redraft}}{\sum_G v}$
- Longevity: $\bar{L}(\text{assets}) = \dfrac{\sum_a v_a\, L(p_a, \text{age}_a)}{\sum_a v_a}$, and $\Delta_{\text{age}} = \bar{L}_R - \bar{L}_G$
- Share of draft picks coming back: $\pi = \dfrac{\sum_{R\ \text{picks}} v}{\sum_R v}$

Strategy term:

| Strategy | Reject if | Term added |
|---|---|---|
| Contend | $\Delta_{\text{now}} \leq 0$ | $\min(\Delta_{\text{now}},\ 1.5)$ |
| Rebuild | $\Delta_{\text{age}} \leq 0$ and $\pi \leq 0$ | $\text{clip}(\Delta_{\text{age}}, 0, 1) + 0.3\,\pi$ |
| Balanced | never | $0.25\,\text{clip}(\Delta_{\text{now}}, -1, 1) + 0.25\,\text{clip}(\Delta_{\text{age}}, -1, 1)$ |

Other terms:

- **Stated needs:** $+0.5$ per needed position gained; $-0.25$ per needed position given away without being regained.
- **Model edge:** $E = \dfrac{\sum_{a \in R} v_a\, e_a - \sum_{a \in G} v_a\, e_a}{\sum_G v}$ with $e_a$ the capped edge fraction from section 7; adds $0.4\,\text{clip}(2E, -1, 1)$.
- **Roster fit:** adds $0.4\,\text{clip}(\text{fit}_{\text{me}}, -1, 1) + 0.4\,\text{clip}(\text{fit}_{\text{partner}}, -1, 1)$.

### 14.3 Diversification

Candidates are sorted by score and greedily selected, subject to: at most 2 trades per partner, no more than 2 uses of any of my assets, and no reuse of a partner asset. This avoids five near-identical suggestions.

### 14.4 Balancers

If a trade is not even, `suggest_balancers` finds assets the short side could add. The added asset would be that side's smallest, so it is discounted by $\delta^{n}$ ($n$ = current number of assets on that side). The needed raw value is

$$
v_{\text{needed}} = \frac{|T_A - T_B|}{\delta^{\,n}}
$$

and the 3 candidate assets (not already in the trade, non-estimated, generic picks preferred) closest to $v_{\text{needed}}$ are suggested.

---

## 15. Results, limitations, and honest caveats

### Results at the last run (README, October 2026)

- **Production model:** MAE about 42 vs 47 (persistence) and 46 (age curve); Spearman 0.71 vs 0.68; 7 walk-forward seasons; band coverage about 77% (nominal 60%).
- **Depth discount:** about 0.65, 90% interval 0.58 to 0.71, from 280 trades.
- **Even threshold:** about 82 (median real trade fairness).
- **Edge model:** out-of-sample $R^2 \approx 0.5$ on log value; signals only beyond $|s| \geq 1.25$.

### Limitations

1. **Accepted is not fair.** Both managers agreeing doesn't prove a trade was balanced. The calibration assumes errors average out.
2. **Today's values for past trades.** Real trades are priced with the latest snapshot. This is a small error for recent trades and grows for old ones.
3. **Small, growing sample.** The trade feed returns only about 50 recent trades per query, so history builds only if the collector runs. Parameters will move as data accumulates. Re-run `python -m datascience.run_all` periodically.
4. **Modest predictive power.** Next-season points are mostly noise. A 42-point MAE is meaningful against a typical range of a few hundred points, but the model is no crystal ball.
5. **Blind spots of the edge model.** Injuries, depth-chart or role changes, coaching changes, and rookies with no NFL history are misjudged or unscored. Treat edges as a prompt to look closer, not advice.
6. **Scoring format.** Features use PPR points from the latest completed season. Other formats reuse the same projections.
7. **Quantile bands aren't calibrated** (77% vs 60%), so the displayed range is conservative.
8. **Heuristic weights are hand-set.** The coefficients in the trade score (0.5, 0.4, 0.25, and so on) are design choices, not fitted. The forward test is the intended way to check them as data accrues.
9. **Terms of use.** Check FantasyCalc, Sleeper and nflverse terms before commercial use.

### Why the design is conservative

| Safeguard | Where | Protects against |
|---|---|---|
| Walk-forward validation | section 6 | Look-ahead bias |
| Out-of-fold edges | section 7 | A model memorizing the price it is meant to judge |
| Baseline comparison | section 6 | Shipping a model that is no better than a naive guess |
| Bootstrap intervals | sections 9, 10 | Over-trusting a single point estimate |
| Significance gate on position multipliers | section 10 | Applying noise as a "market inefficiency" |
| Caps (edge fraction 0.5, multipliers 0.9 to 1.1, bench cap) | sections 7, 8, 10 | One outlier dominating |
| Minimum trade count (40) | section 9 | Overfitting a tiny sample |
| Safe defaults when artifacts are missing | `insights.py` | The app breaking without the pipeline |

---

## 16. Glossary

- **Walk-forward validation:** train only on the past, test on the next period, repeat.
- **Data leakage:** information from the future (or from the target) sneaking into training.
- **Out-of-fold (OOF):** a prediction for a row from a model that was trained without that row.
- **Gradient boosting:** an ensemble that adds small trees one at a time, each fixing the previous ensemble's errors.
- **Quantile regression:** predicting a percentile (for example the 20th) instead of the mean.
- **Residual:** actual minus predicted (here, in log space).
- **z-score:** a value expressed in standard deviations from the mean.
- **Ridge regression:** linear regression with an L2 penalty $\lambda\|\beta\|^2$ that shrinks coefficients.
- **Bootstrap:** resampling the data with replacement to see how much an estimate varies.
- **Spearman correlation:** correlation of rankings, ignoring the exact values.
- **MAE / RMSE:** mean absolute error / root mean squared error (RMSE punishes big misses more).
- **PPR:** points per reception.
- **VORP:** value over replacement player.
- **Replacement level:** the value of the best player a team could get for free.
- **Dynasty value (FantasyCalc):** a market-derived number for a player's long-term trade worth.
- **Depth decay ($\delta$):** the factor by which each additional asset on one side of a trade is discounted.

---

## Quick explainer (if anyone asks)

**What is the data science here, in one breath?**
The app uses market trade values from FantasyCalc, then adds a layer of statistics on top. It learns how players age, predicts how many points they will score next season, checks whether the market's price looks high or low compared to similar players, and tunes the trade-fairness math using real trades that actually happened.

**The four things it does**

1. **Aging curves.** Looks at years of NFL history to see how much production a QB, RB, WR or TE keeps each year as they get older. Used to judge how long a player's value will last.
2. **Next-season prediction.** A gradient-boosted model (many small decision trees) predicts next year's fantasy points from age, recent production, usage, and draft position. It also gives a likely range. It is tested by training only on past seasons and predicting the next one, and it beats "same as last year" by a modest margin. Next-season fantasy production is mostly luck and injuries, so no model can be very precise.
3. **Buy low / sell high.** A second model learns what the market usually pays for a given profile. If a player's real price is far above or below that, he gets flagged. Every player is scored by a model that never saw him, so it can't just memorize prices. Flags only appear for big gaps. It is a prompt to look closer, not a guarantee.
4. **Trade fairness tuning.** Two mid-level players are not worth one star, so extra assets on a side are discounted. The discount (about 0.65 per extra asset) was found by testing which value made real accepted trades look most balanced. Resampling the trades many times (a bootstrap) shows how much to trust the number.

**Why you can trust it (and where not to)**

- It is always compared to simple baselines, so it has to earn its place.
- It is tested on the future, not on data it trained on.
- Adjustments only apply when they are statistically distinguishable from noise, and they are capped.
- Limits: accepted trades aren't proven fair, the trade sample is small, and the model can't see injuries, role changes, or rookies with no NFL history.

**Short version for a one-line answer:**
"It's a market-value trade calculator with a statistics layer: aging curves, a next-season points model, a market-vs-fundamentals buy/sell signal, and fairness math calibrated on real trades. It is conservative on purpose, and its predictions are modest because fantasy football is noisy."

---

## Whole-project summary (everything, not just the data science)

**What the project is**
A small Flask web app, the Dynasty Trade Analyzer. It prices dynasty fantasy football trades and finds balanced trades in your Sleeper league. There is no database and no user accounts.

**What users can do**
- **Trade calculator:** enter players and picks for each side (for example `Ja'Marr Chase` and `2027 1st`). You get a fairness score, a verdict (even, favors A, favors B), and assets that could balance the deal. It works without a league. It has autocomplete, trade history saved in the browser, and signed share links (nothing is stored on the server).
- **Trade suggestions:** connect a Sleeper league, pick your team, choose a strategy (contend, rebuild, balanced), how even trades must be (risk), and positions you need. The app searches every roster for 1-for-1, 2-for-1 and 1-for-2 trades.
- **League-aware values:** values adjust for Superflex vs 1 QB, PPR scoring, league size and tight end premium. Draft pick ownership, including traded picks, comes from Sleeper.
- **Insights page:** the buy/sell lists, aging curves and calibration results from the data science layer.
- **Optional AI explanations:** with `OPENAI_API_KEY` set, a model writes short explanations of results. It never sets values, picks players or scores fairness. Without a key, built-in text is used.

**How it works, in layers**

| Layer | What it does | Where |
|---|---|---|
| Data sources | Sleeper (leagues, rosters, picks), FantasyCalc (trade values), nflverse (NFL stats), DynastyProcess (player ids) | `sleeper.py`, `values.py`, `datascience/collect.py` |
| Values | Matches names and picks, applies league format, position multipliers and the TE premium, adds estimated bench values | `values.py` |
| Trade math | Depth-discounted totals, fairness score, verdict, balancing assets | `trades.py` |
| Suggestion search | Tries 1- and 2-asset packages per partner, scores them by strategy, needs, model edge and roster fit, then diversifies the results | `trades.py` |
| Roster model | Greedy lineups, replacement level, need, surplus and VORP | `needs.py` |
| Data science artifacts | Aging curves, edge signals, calibrated constants, value history, all stored as JSON | `datascience/` writes, `insights.py` reads |
| Web layer | Routes, templates, CSRF, security headers, error handlers, request ids | `routes.py`, `__init__.py`, `templates/`, `static/` |
| Infrastructure | TTL cache, rate limiter, config, optional LLM | `cache.py`, `config.py`, `llm.py` |

**State and caching**
- League data: fetched from Sleeper and cached in memory for 5 minutes.
- Trade values: fetched from FantasyCalc, cached for 6 hours, and mirrored to `.cache/*.json` so the app survives an outage.
- Selected league and team: a signed session cookie (ids and display names only).
- Rate limits: in memory, per process.
- Restarting loses nothing important.

**Safety, security and reliability**
- Rate limits on form submissions and on AI calls.
- Signed cookies, CSRF protection and security headers. `SECRET_KEY` is required in production.
- Every request is logged with a request id, which is also returned in the `X-Request-ID` header.
- If FantasyCalc is down and nothing is cached, the app shows a clear message instead of crashing.
- If the data science artifacts are missing, `insights.py` falls back to safe defaults (depth decay 0.92, even threshold 90), so the app still works on plain market values.
- The web app never imports pandas or scikit-learn.

**Quality and tooling**
- Tests in `tests/` (pytest, no network needed). The data science tests skip themselves unless `requirements-ds.txt` is installed.
- Linting and typing with `ruff` and `mypy`.
- Lock files for reproducible installs (`requirements.lock`, `requirements-ds.lock`).
- Notebooks in `notebooks/` walk through the analysis, with charts in `notebooks/figures/`.
- `scripts/verify_league.py` sanity-checks the app against a real league from the command line.

**Running and deploying**
- Local: create a venv, `pip install -r requirements-dev.txt`, then `python run.py`, and open http://127.0.0.1:5000.
- Refresh the models: `python -m datascience.run_all` (collects data, refits models, rewrites the JSON artifacts and figures).
- Keep data accumulating: run `python -m datascience.collect snapshots` daily and `python -m datascience.collect trades` regularly.
- Production: a single web service (`gunicorn wsgi:app`), with no database. See `DEPLOYMENT.md`.

**Honest limitations**
- FantasyCalc is the only value source. If its API changes, cached values keep working and the app degrades with a message.
- Caches and rate limits are per worker, so with several workers rate limits are approximate.
- Picks are priced only for 2027 to 2029, and player names must match FantasyCalc spellings (punctuation, accents and Jr/Sr are ignored).
- Data science limits are listed in section 15.
- Check the terms of FantasyCalc, Sleeper and nflverse before commercial use, and keep the credit in the footer. The project is MIT licensed.
