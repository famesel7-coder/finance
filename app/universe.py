from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Company:
    ticker: str
    name: str
    sector: str


MOEX_COMPANIES = [
    Company("SBER", "Сбербанк", "Финансы"),
    Company("GAZP", "Газпром", "Нефть и газ"),
    Company("LKOH", "Лукойл", "Нефть и газ"),
    Company("ROSN", "Роснефть", "Нефть и газ"),
    Company("NVTK", "НОВАТЭК", "Нефть и газ"),
    Company("GMKN", "Норникель", "Металлы"),
    Company("PLZL", "Полюс", "Металлы"),
    Company("PHOR", "ФосАгро", "Химия"),
    Company("CHMF", "Северсталь", "Металлы"),
    Company("NLMK", "НЛМК", "Металлы"),
    Company("MAGN", "ММК", "Металлы"),
    Company("MTSS", "МТС", "Телеком"),
    Company("TATN", "Татнефть", "Нефть и газ"),
    Company("X5", "X5", "Ритейл"),
    Company("OZON", "Ozon", "E-commerce"),
    Company("YDEX", "Яндекс", "Технологии"),
    Company("MOEX", "Московская биржа", "Финансы"),
    Company("IRAO", "Интер РАО", "Энергетика"),
    Company("RUAL", "РУСАЛ", "Металлы"),
    Company("ALRS", "АЛРОСА", "Металлы"),
    Company("TRNFP", "Транснефть ап", "Нефть и газ"),
    Company("AFLT", "Аэрофлот", "Транспорт"),
]

DEFAULT_MOEX_TICKERS = [company.ticker for company in MOEX_COMPANIES]
COMPANY_BY_TICKER = {company.ticker: company for company in MOEX_COMPANIES}


def public_universe() -> list[dict]:
    return [asdict(company) for company in MOEX_COMPANIES]
