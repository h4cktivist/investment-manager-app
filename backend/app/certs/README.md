# Доверенные сертификаты

Здесь лежат официальные сертификаты российского национального
удостоверяющего центра (Минцифры). Они нужны, потому что MOEX ISS и
T-Invest API отдают цепочку, выпущенную этим УЦ, а корневого сертификата
нет в бандле `certifi`.

Источник: <https://gu-st.ru/content/lending/>

* `russian_trusted_root_ca.pem` — корневой сертификат
  («Russian Trusted Root CA»);
* `russian_trusted_sub_ca.pem` — промежуточный сертификат
  («Russian Trusted Sub CA»).

Файлы подключаются в `app/services/tls.py` к стандартному набору
`certifi`. Проверка TLS не отключается. Свой бандл можно задать
переменной окружения `SSL_CA_BUNDLE`.
