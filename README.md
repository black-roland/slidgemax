# SlidgeMAX

XMPP-шлюз (legacy-модуль) для мессенджера MAX, построенный на [Slidge](https://slidge.im/).

Поддерживает личные чаты 1:1, редактирование сообщений и текстовые уведомления о звонках.

> [!WARNING]
> Это **неофициальный** и **экспериментальный** шлюз. SlidgeMAX использует клиент [PyMax](https://github.com/MaxApiTeam/PyMax), который работает через неофициальный внутренний API. Этот API может измениться без предупреждения, а использование может нарушать условия сервиса.
> Вы используете SlidgeMAX на свой риск. Авторы и контрибьюторы не несут ответственности за блокировки аккаунтов, потерю данных или другие последствия.

<img width="906" height="502" alt="Чаты в DinoX с поддержкой SlidgeMAX" src="https://github.com/user-attachments/assets/510844b4-0532-4903-9921-b67f1b1a6511" />

## Что поддерживается

- Контакты и никнеймы.
- Боты MAX объявляются в XMPP как client type `bot`; остальные контакты — `phone`. Личный диалог, в том числе с ботом, добавляется в ростер, даже если MAX не позволяет добавить его в контакты.
- Личные сообщения.
- Редактирование сообщений (XEP-0308).
- Текстовые уведомления о входящих звонках.
- Регистрация по SMS и опциональному 2FA-паролю.
- Текст-заглушка, когда MAX присылает неподдерживаемые медиа.
- Аватары контактов показываются, когда MAX присылает `base_url` и `photo_id`; заполнение ростера не дожидается загрузки.
- Ваш XMPP-статус публикуется в MAX как «в сети» только для available и chat; away, dnd и офлайн — не в сети. Текст статуса не передаётся. Обновление уходит со следующим ping, до 30 секунд.

## Что не поддерживается

Группы, каналы, `WebClient` / QR-вход, реакции, стикеры, опросы, файлы, голосовые сообщения, полноценные звонки, форматирование.

## Требования

- Python ≥3.13 (рекомендуется 3.14).
- XMPP-сервер с поддержкой внешних компонентов (рекомендуется Prosody).
- uv или pip.

## Установка на RHEL/Fedora

Slidge не является отдельным демоном. Это Python-библиотека, которая подтягивается при установке этого пакета. Systemd-юнит запускает `/opt/slidgemax/bin/slidgemax`, который вызывает Slidge с `--legacy-module slidgemax`.

```bash
# от root
dnf install epel-release
dnf install uv python3.14

git clone https://github.com/black-roland/slidgemax /opt/slidgemax-src
cd /opt/slidgemax-src
uv venv /opt/slidgemax --python 3.14
uv pip install --python /opt/slidgemax/bin/python .

# Slidge оказывается в том же venv:
/opt/slidgemax/bin/python -c 'import slidge, slidgemax; print(slidge.__version__, slidgemax.__version__)'
/opt/slidgemax/bin/slidgemax --help
```

Каталог `/opt/slidgemax` должен принадлежать root и быть доступным для выполнения всем. Пользователю сервиса нужно только запускать этот интерпретатор; писать туда он не должен. Состояние (база Slidge, файлы сессий PyMax) хранится в `/var/lib/slidgemax/<jid>/`.

Для обновления сделайте pull и переустановите в тот же venv:

```bash
cd /opt/slidgemax-src
git pull
uv pip install --python /opt/slidgemax/bin/python .
systemctl restart slidgemax@max.example.org.service
```

### Systemd-сервис

```bash
# от root, после установки /opt/slidgemax
useradd --system --home-dir /var/lib/slidgemax --shell /usr/sbin/nologin slidgemax
install -d -o slidgemax -g slidgemax -m 0750 /var/lib/slidgemax

cp contrib/systemd/slidgemax@.service /etc/systemd/system/

# Обязательный файл для экземпляра. Опциональные общие настройки: /etc/sysconfig/slidgemax
cat > /etc/sysconfig/slidgemax-max.example.org <<EOF
MAX_COMPONENT_SECRET=your-component-secret
EOF
chmod 640 /etc/sysconfig/slidgemax-max.example.org
chown root:slidgemax /etc/sysconfig/slidgemax-max.example.org

systemctl daemon-reload
systemctl enable --now slidgemax@max.example.org.service
journalctl -u slidgemax@max.example.org -f
```

`ExecStart` — это `/opt/slidgemax/bin/slidgemax`. Этот скрипт запускает Slidge; не устанавливайте Slidge отдельно для этого юнита. Prosody должен использовать тот же JID и тот же `MAX_COMPONENT_SECRET`.

## Prosody

```lua
Component "max.example.org"
    component_secret = "the-same-secret-as-the-ini"
```

Привилегированная сущность (XEP-0356) рекомендуется, чтобы шлюз мог добавлять контакты MAX в ростер пользователя. См. [документацию Slidge по привилегиям](https://slidge.im/docs/matridge/main/admin/privileges.html).

## Регистрация

1. Найдите шлюз (`max.example.org`).
2. Выполните ad-hoc команду **Register**.
3. Укажите номер телефона, при необходимости 2FA-пароль, затем SMS-код.
4. Контакты появятся в ростере как `id@max.example.org`.

**Gajim:** Accounts → Discover services → шлюз → Execute command.

## Ограничения

- Только 1:1 (без групповых чатов и каналов)
- Текст, редактирование, уведомления о звонках и присутствие/последнее посещение.
- Нет поддержки файлов, голоса, стикеров, реакций, карточек.

## Вклад в проект

[Как внести вклад](CONTRIBUTING.md).

## Разработка

```bash
uv sync
uv run slidge \
  --legacy-module slidgemax \
  --jid max.example.org \
  --secret "shared-secret" \
  --home-dir ./data \
  --server 127.0.0.1 --port 5347
```

### Тесты

```bash
pip install -e ".[dev]"
pytest
```

## Лицензия

Apache-2.0
