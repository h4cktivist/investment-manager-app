from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - удобство логов
        return self.value


class PortfolioKind(StrEnum):
    BROKER = "BROKER"
    IIS = "IIS"
    BANK = "BANK"
    OTHER = "OTHER"


class AssetClass(StrEnum):
    SHARE = "SHARE"
    BOND = "BOND"
    ETF = "ETF"
    CURRENCY = "CURRENCY"
    DEPOSIT = "DEPOSIT"      # банковский вклад / накопительный счёт
    CUSTOM = "CUSTOM"        # всё остальное, что ведём руками


class TxType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    COUPON = "COUPON"
    AMORTIZATION = "AMORTIZATION"
    SPLIT = "SPLIT"
    DEPOSIT = "DEPOSIT"        # ввод денег в портфель
    WITHDRAWAL = "WITHDRAWAL"  # вывод денег из портфеля
    TAX = "TAX"
    FEE = "FEE"
    INTEREST = "INTEREST"      # проценты по вкладу / остатку


#: события, приносящие деньги без движения бумаг
INCOME_TYPES = {TxType.DIVIDEND, TxType.COUPON, TxType.AMORTIZATION, TxType.INTEREST}
#: внешние потоки — не считаются доходностью, но влияют на XIRR/TWR
EXTERNAL_FLOW_TYPES = {TxType.DEPOSIT, TxType.WITHDRAWAL}
TRADE_TYPES = {TxType.BUY, TxType.SELL}


class PaymentKind(StrEnum):
    COUPON = "COUPON"
    AMORTIZATION = "AMORTIZATION"
    DIVIDEND = "DIVIDEND"
    MATURITY = "MATURITY"
    OFFER = "OFFER"
    CUSTOM_INCOME = "CUSTOM_INCOME"


class PayoutFrequency(StrEnum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    SEMIANNUAL = "SEMIANNUAL"
    ANNUAL = "ANNUAL"
    AT_MATURITY = "AT_MATURITY"
    NONE = "NONE"

    @property
    def per_year(self) -> int:
        return {
            "MONTHLY": 12,
            "QUARTERLY": 4,
            "SEMIANNUAL": 2,
            "ANNUAL": 1,
            "AT_MATURITY": 1,
            "NONE": 0,
        }[self.value]


class BenchmarkKind(StrEnum):
    EQUITY = "EQUITY"
    BOND_GOV = "BOND_GOV"
    BOND_CORP = "BOND_CORP"
    MONEY = "MONEY"
    MIXED = "MIXED"


class ImportStatus(StrEnum):
    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"
