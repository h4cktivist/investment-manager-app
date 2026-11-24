"""Единая настройка TLS для внешних HTTP-клиентов.

MOEX ISS и T-Invest API отдают цепочку сертификатов, выпущенную
российским национальным удостоверяющим центром
(Russian Trusted Sub CA -> Russian Trusted Root CA). Корневого
сертификата нет в бандле certifi, поэтому httpx по умолчанию падает с
``CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain``.

Мы не отключаем проверку TLS: к стандартному набору certifi добавляем
официальный корневой сертификат Минцифры (файлы лежат в ``app/certs``).
Если нужен собственный CA-bundle (например, корпоративный прокси),
путь к нему задаётся переменной окружения ``SSL_CA_BUNDLE``.
"""
from __future__ import annotations

import logging
import ssl
from functools import lru_cache
from pathlib import Path

import certifi

from app.config import settings

log = logging.getLogger(__name__)

#: Каталог с дополнительными доверенными сертификатами, которые идут в репозитории
CERTS_DIR = Path(__file__).resolve().parent.parent / "certs"


def _bundled_ca_files() -> list[Path]:
    if not CERTS_DIR.is_dir():
        return []
    return sorted(CERTS_DIR.glob("*.pem"))


@lru_cache(maxsize=1)
def external_ssl_context() -> ssl.SSLContext:
    """SSL-контекст для запросов к внешним API.

    По умолчанию: certifi + корневой сертификат российского УЦ.
    Если задан ``SSL_CA_BUNDLE`` — используется только указанный файл
    (удобно для корпоративного прокси с собственным CA).
    Проверка сертификата и имени хоста всегда включена.
    """
    custom_bundle = settings.ssl_ca_bundle.strip()
    if custom_bundle:
        bundle = Path(custom_bundle).expanduser()
        if not bundle.is_file():
            raise FileNotFoundError(
                f"SSL_CA_BUNDLE указывает на несуществующий файл: {bundle}"
            )
        context = ssl.create_default_context(cafile=str(bundle))
    else:
        context = ssl.create_default_context(cafile=certifi.where())
        for extra in _bundled_ca_files():
            try:
                context.load_verify_locations(cafile=str(extra))
            except ssl.SSLError as exc:  # pragma: no cover - битый файл в поставке
                log.warning("не удалось загрузить CA %s: %s", extra.name, exc)

    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context
