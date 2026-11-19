"""Метрики доходности и риска.

Соглашения:
* TWR считается по дневным доходностям с денежными потоками «в начале дня»:
  r_t = V_t / (V_{t-1} + F_t) − 1. Это единственный корректный вариант, когда
  первый день начинается с нулевого портфеля и пополнения.
* XIRR — денежно-взвешенная доходность с точки зрения инвестора: пополнение
  портфеля отрицательный поток, вывод и итоговая стоимость — положительные.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from app.services.portfolio import ValuationPoint

EPS = 1e-12
TRADING_DAYS = 252


@dataclass
class ReturnSeries:
    dates: list[date]
    daily: list[float]           # дневные доходности
    index: list[float]           # накопленный индекс, старт = 1.0

    @property
    def total(self) -> float:
        return self.index[-1] - 1.0 if self.index else 0.0


def twr_returns(
    points: list[ValuationPoint], opening_value: float | None = None
) -> ReturnSeries:
    """Дневные доходности портфеля, очищенные от вводов/выводов.

    ``opening_value`` — стоимость портфеля на день перед началом периода.
    Без неё расчёт за короткий период стартовал бы от случайного платежа
    первого дня и давал бы бессмысленные тысячи процентов.
    """
    dates: list[date] = []
    daily: list[float] = []
    index: list[float] = []
    acc = 1.0
    prev_total: float | None = opening_value if (opening_value or 0) > EPS else None

    for point in points:
        if prev_total is None:
            # портфель начинается с нуля: базой служит сам ввод денег
            base = point.external_flow
            r = (point.total / base - 1.0) if base > EPS else 0.0
        else:
            base = prev_total + point.external_flow
            r = (point.total / base - 1.0) if base > EPS else 0.0
        # защита от мусорных выбросов при обнулении портфеля
        if not math.isfinite(r):
            r = 0.0
        acc *= 1.0 + r
        dates.append(point.day)
        daily.append(r)
        index.append(acc)
        prev_total = point.total

    return ReturnSeries(dates=dates, daily=daily, index=index)


MIN_ANNUALIZE_DAYS = 30


def annualize(total_return: float, days: int) -> float | None:
    """Приводит доходность к году. На окне меньше месяца не считаем —
    возведение в степень 1/years превращает шум в тысячи процентов."""
    if days < MIN_ANNUALIZE_DAYS:
        return None
    base = 1.0 + total_return
    if base <= 0:
        return -1.0
    years = days / 365.25
    return base ** (1.0 / years) - 1.0


def stdev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def volatility(daily: list[float], trading_days: int = TRADING_DAYS) -> float:
    """Годовая волатильность из дневных доходностей."""
    return stdev(daily) * math.sqrt(trading_days)


def downside_deviation(
    daily: list[float], mar_daily: float, trading_days: int = TRADING_DAYS
) -> float:
    below = [min(r - mar_daily, 0.0) for r in daily]
    if len(below) < 2:
        return 0.0
    var = sum(v**2 for v in below) / (len(below) - 1)
    return math.sqrt(var) * math.sqrt(trading_days)


def percentile(values: list[float], q: float) -> float:
    """Квантиль с линейной интерполяцией между соседними точками."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * min(max(q, 0.0), 1.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def historical_var(daily: list[float], level: float = 0.95) -> float | None:
    """Исторический VaR: граница худших (1−level) дневных доходностей.

    Возвращается доходность со знаком: −0,02 значит «потеря до 2 %».
    Уровень 0.95 — 5 % худших дней.
    """
    if len(daily) < 2:
        return None
    return percentile(daily, 1.0 - level)


def expected_shortfall(daily: list[float], level: float = 0.95) -> float | None:
    """Expected Shortfall (CVaR): средний убыток за границей VaR."""
    if len(daily) < 2:
        return None
    var = historical_var(daily, level)
    if var is None:
        return None
    tail = [r for r in daily if r <= var]
    if not tail:
        return var
    return sum(tail) / len(tail)


def annualize_risk(value: float | None, trading_days: int = TRADING_DAYS) -> float | None:
    """Оценка дневного риска в годовом выражении (масштаб √252)."""
    if value is None:
        return None
    return value * math.sqrt(trading_days)


def covariance(a: list[float], b: list[float]) -> float | None:
    """Выборочная ковариация дневных доходностей (несмещённая)."""
    n = min(len(a), len(b))
    if n < 3:
        return None
    x, y = a[:n], b[:n]
    mx = sum(x) / n
    my = sum(y) / n
    return sum((x[i] - mx) * (y[i] - my) for i in range(n)) / (n - 1)


def correlation(a: list[float], b: list[float]) -> float | None:
    """Корреляция Пирсона; None, если ряд вырожденный (нет движения)."""
    n = min(len(a), len(b))
    if n < 3:
        return None
    cov = covariance(a, b)
    if cov is None:
        return None
    sx = stdev(a[:n])
    sy = stdev(b[:n])
    if sx < EPS or sy < EPS:
        return None
    return max(-1.0, min(1.0, cov / (sx * sy)))


def pair_statistics(a: list[float], b: list[float]) -> dict | None:
    """Корреляция, ковариация и число наблюдений для пары активов."""
    n = min(len(a), len(b))
    if n < 3:
        return None
    cov = covariance(a, b)
    corr = correlation(a, b)
    if cov is None or corr is None:
        return None
    return {
        "observations": n,
        "correlation": corr,
        "covariance": cov,
        "covariance_annual": cov * TRADING_DAYS,
    }


def calmar(annual_return: float | None, max_dd: float) -> float | None:
    """Calmar: годовая доходность на единицу максимальной просадки."""
    if annual_return is None or max_dd >= -EPS:
        return None
    return annual_return / abs(max_dd)


def sharpe(annual_return: float, annual_vol: float, risk_free: float) -> float | None:
    if annual_vol < EPS:
        return None
    return (annual_return - risk_free) / annual_vol


def sortino(
    annual_return: float, daily: list[float], risk_free: float, trading_days: int = TRADING_DAYS
) -> float | None:
    mar_daily = (1.0 + risk_free) ** (1.0 / trading_days) - 1.0
    dd = downside_deviation(daily, mar_daily, trading_days)
    if dd < EPS:
        return None
    return (annual_return - risk_free) / dd


def beta_alpha(
    portfolio: list[float], benchmark: list[float], risk_free: float = 0.0
) -> tuple[float | None, float | None, float | None]:
    """Бета, альфа (годовая) и корреляция против бенчмарка."""
    n = min(len(portfolio), len(benchmark))
    if n < 3:
        return None, None, None
    p = portfolio[-n:]
    b = benchmark[-n:]
    mp = sum(p) / n
    mb = sum(b) / n
    cov = sum((p[i] - mp) * (b[i] - mb) for i in range(n)) / (n - 1)
    var_b = sum((b[i] - mb) ** 2 for i in range(n)) / (n - 1)
    if var_b < EPS:
        return None, None, None
    beta = cov / var_b

    sp = stdev(p)
    sb = stdev(b)
    corr = cov / (sp * sb) if sp > EPS and sb > EPS else None

    rf_daily = (1.0 + risk_free) ** (1.0 / TRADING_DAYS) - 1.0
    alpha_daily = (mp - rf_daily) - beta * (mb - rf_daily)
    alpha_annual = (1.0 + alpha_daily) ** TRADING_DAYS - 1.0
    return beta, alpha_annual, corr


def max_drawdown(index: list[float]) -> tuple[float, date | None, date | None]:
    """Максимальная просадка накопленного индекса."""
    if not index:
        return 0.0, None, None
    peak = index[0]
    worst = 0.0
    peak_i = 0
    worst_peak_i = 0
    worst_i = 0
    for i, value in enumerate(index):
        if value > peak:
            peak = value
            peak_i = i
        if peak > EPS:
            dd = value / peak - 1.0
            if dd < worst:
                worst = dd
                worst_peak_i = peak_i
                worst_i = i
    return worst, worst_peak_i, worst_i


def xirr(flows: list[tuple[date, float]], guess: float = 0.1) -> float | None:
    """Внутренняя норма доходности с учётом дат (аналог XIRR в Excel)."""
    flows = [(d, a) for d, a in flows if abs(a) > EPS]
    if len(flows) < 2:
        return None
    if not (any(a > 0 for _, a in flows) and any(a < 0 for _, a in flows)):
        return None

    flows.sort(key=lambda x: x[0])
    t0 = flows[0][0]
    times = [(d - t0).days / 365.0 for d, _ in flows]
    amounts = [a for _, a in flows]

    def npv(rate: float) -> float:
        total = 0.0
        for amount, t in zip(amounts, times):
            total += amount / (1.0 + rate) ** t
        return total

    # Ньютон
    rate = guess
    for _ in range(60):
        if rate <= -0.999999:
            break
        value = npv(rate)
        derivative = 0.0
        for amount, t in zip(amounts, times):
            derivative -= t * amount / (1.0 + rate) ** (t + 1.0)
        if abs(derivative) < EPS:
            break
        step = value / derivative
        new_rate = rate - step
        if not math.isfinite(new_rate):
            break
        if abs(new_rate - rate) < 1e-9:
            return new_rate
        rate = max(new_rate, -0.9999)

    # запасная бисекция
    lo, hi = -0.9999, 100.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def money_weighted_return(
    external_flows: list[tuple[date, float]],
    final_value: float,
    as_of: date,
    opening_value: float | None = None,
    opening_date: date | None = None,
) -> float | None:
    """XIRR портфеля: пополнения — минус, вывод и остаток — плюс.

    Если период начинается не с нуля, стоимость портфеля на его начало
    входит в поток как первоначальное вложение.
    """
    flows = [(d, -amount) for d, amount in external_flows]
    if opening_value and opening_value > EPS and opening_date is not None:
        flows.append((opening_date, -opening_value))
    if final_value > EPS:
        flows.append((as_of, final_value))
    return xirr(flows)


@dataclass
class MetricsBundle:
    start: date | None
    end: date | None
    days: int
    observations: int
    twr: float
    twr_annual: float | None
    xirr: float | None
    volatility: float
    max_drawdown: float
    max_drawdown_start: date | None
    max_drawdown_end: date | None
    max_drawdown_days: int | None
    var_95_daily: float | None
    es_95_daily: float | None
    var_99_daily: float | None
    es_99_daily: float | None
    var_95_annual: float | None
    es_95_annual: float | None
    var_99_annual: float | None
    es_99_annual: float | None
    worst_day: float | None
    worst_day_date: date | None
    best_day: float | None
    best_day_date: date | None
    sharpe: float | None
    sortino: float | None
    calmar: float | None
    beta: float | None
    alpha: float | None
    correlation: float | None
    benchmark_code: str | None
    benchmark_return: float | None
    risk_free_rate: float


def compute_metrics(
    points: list[ValuationPoint],
    external_flows: list[tuple[date, float]],
    final_value: float,
    risk_free: float,
    benchmark_daily: list[float] | None = None,
    benchmark_code: str | None = None,
    benchmark_total: float | None = None,
    opening_value: float | None = None,
) -> MetricsBundle:
    if not points:
        return MetricsBundle(
            start=None, end=None, days=0, observations=0, twr=0.0,
            twr_annual=None, xirr=None, volatility=0.0, max_drawdown=0.0,
            max_drawdown_start=None, max_drawdown_end=None,
            max_drawdown_days=None, var_95_daily=None, es_95_daily=None,
            var_99_daily=None, es_99_daily=None, var_95_annual=None,
            es_95_annual=None, var_99_annual=None, es_99_annual=None,
            worst_day=None, worst_day_date=None, best_day=None,
            best_day_date=None, sharpe=None, sortino=None, calmar=None,
            beta=None, alpha=None, correlation=None,
            benchmark_code=benchmark_code, benchmark_return=benchmark_total,
            risk_free_rate=risk_free,
        )

    series = twr_returns(points, opening_value=opening_value)
    start, end = points[0].day, points[-1].day
    days = (end - start).days or 1
    total = series.total
    annual = annualize(total, days)
    vol = volatility(series.daily)
    dd, dd_from, dd_to = max_drawdown(series.index)
    observations = len(series.daily)

    # На коротком окне крайние квантили — это просто минимум ряда,
    # поэтому 99 % показываем только при достаточной истории.
    var_95 = historical_var(series.daily, 0.95) if observations >= 20 else None
    es_95 = expected_shortfall(series.daily, 0.95) if observations >= 20 else None
    var_99 = historical_var(series.daily, 0.99) if observations >= 60 else None
    es_99 = expected_shortfall(series.daily, 0.99) if observations >= 60 else None

    worst_day = worst_day_date = best_day = best_day_date = None
    if series.daily:
        worst_i = min(range(observations), key=series.daily.__getitem__)
        best_i = max(range(observations), key=series.daily.__getitem__)
        worst_day, worst_day_date = series.daily[worst_i], series.dates[worst_i]
        best_day, best_day_date = series.daily[best_i], series.dates[best_i]

    dd_days = None
    if isinstance(dd_from, int) and isinstance(dd_to, int) and series.dates:
        dd_days = (series.dates[dd_to] - series.dates[dd_from]).days

    beta = alpha = corr = None
    if benchmark_daily:
        beta, alpha, corr = beta_alpha(series.daily, benchmark_daily, risk_free)

    return MetricsBundle(
        start=start,
        end=end,
        days=days,
        observations=observations,
        twr=total,
        twr_annual=annual,
        xirr=money_weighted_return(
            external_flows, final_value, end, opening_value=opening_value, opening_date=start
        ),
        volatility=vol,
        max_drawdown=dd,
        max_drawdown_start=series.dates[dd_from] if isinstance(dd_from, int) and series.dates else None,
        max_drawdown_end=series.dates[dd_to] if isinstance(dd_to, int) and series.dates else None,
        max_drawdown_days=dd_days,
        var_95_daily=var_95,
        es_95_daily=es_95,
        var_99_daily=var_99,
        es_99_daily=es_99,
        var_95_annual=annualize_risk(var_95),
        es_95_annual=annualize_risk(es_95),
        var_99_annual=annualize_risk(var_99),
        es_99_annual=annualize_risk(es_99),
        worst_day=worst_day,
        worst_day_date=worst_day_date,
        best_day=best_day,
        best_day_date=best_day_date,
        sharpe=sharpe(annual, vol, risk_free) if annual is not None else None,
        sortino=sortino(annual, series.daily, risk_free) if annual is not None else None,
        calmar=calmar(annual, dd),
        beta=beta,
        alpha=alpha,
        correlation=corr,
        benchmark_code=benchmark_code,
        benchmark_return=benchmark_total,
        risk_free_rate=risk_free,
    )
