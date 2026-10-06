from collections.abc import Iterable
from decimal import Decimal
from uuid import UUID
from datetime import datetime
from typing import TypedDict, Unpack, final, override, TYPE_CHECKING

from django.db import models
from django.utils.timezone import localtime
from solo.models import SingletonModel

from core.domain.enums import TransactionStatus, TransactionType
from core.integrations.fragment.enums import FragmentStatus


TARGET_SELF = "Себе"


class SaveKwargs(TypedDict, total=False):
    force_insert: bool
    force_update: bool
    using: str | None
    update_fields: Iterable[str] | None


@final
class PromoCode(models.Model):
    if TYPE_CHECKING:
        telegram_users: models.manager.RelatedManager["TelegramUser"]  # pyright: ignore[reportUninitializedInstanceVariable]
        id: int  # pyright: ignore[reportUninitializedInstanceVariable]

    name = models.CharField(max_length=50, verbose_name="Промокод", help_text="Только название; регистр учитывается")
    discount = models.DecimalField(
        default=Decimal("0.00"),
        decimal_places=2,
        max_digits=5,
        verbose_name="Скидка %",
        help_text="Указывается в %, например, 5.00 (не 0.05)"
    )
    usage_account = models.BigIntegerField(null=True, blank=True, verbose_name="На аккаунт", help_text="Если пусто - без ограничений")
    usage_global = models.BigIntegerField(null=True, blank=True, verbose_name="Всего", help_text="Если пусто - без ограничений")
    is_active = models.BooleanField(default=True, verbose_name="Активен?")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Последнее обновление")

    @override
    def __str__(self):
        return f"{self.name} {self.discount}%"

    @final
    class Meta:
        verbose_name = "Промокод"
        verbose_name_plural = "Промокоды"


@final
class TelegramUser(models.Model):
    if TYPE_CHECKING:
        transactions: models.manager.RelatedManager["Transaction"]  # pyright: ignore[reportUninitializedInstanceVariable]

    telegram_id = models.BigIntegerField(unique=True, verbose_name="Telegram ID")
    username = models.CharField(max_length=255, blank=True, verbose_name="Username")

    active_promo = models.ForeignKey(
        PromoCode,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="telegram_users",
        verbose_name="Актив. промокод"
    )
    promo_since = models.DateTimeField(null=True, blank=True, verbose_name="Дата активации промокода")

    is_active = models.BooleanField(default=True, blank=True, verbose_name="Активный пользователь?", help_text="Пользователь неактивен, если у бота нет к нему доступа")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата регистрации")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Последнее обновление")

    @override
    def save(self, **kwargs: Unpack[SaveKwargs]):  # noqa  # pyright: ignore[reportIncompatibleMethodOverride]
        self.username = self.username.lstrip("@")
        super().save(**kwargs)

    @override
    async def asave(self, **kwargs: Unpack[SaveKwargs]):  # noqa  # pyright: ignore[reportIncompatibleMethodOverride]
        self.username = self.username.lstrip("@")
        await super().asave(**kwargs)

    @override
    def __str__(self):
        return f"{self.username or self.telegram_id}"

    @final
    class Meta:
        verbose_name = "Пользователь бота"
        verbose_name_plural = "Пользователи бота"


class Transaction(models.Model):
    if TYPE_CHECKING:
        metadata_info: "TransactionMetadata"  # pyright: ignore[reportUninitializedInstanceVariable]
        metadata_info_id: int  # pyright: ignore[reportUninitializedInstanceVariable]

    id: models.UUIDField[UUID] = models.UUIDField(primary_key=True, verbose_name="ID платежа", help_text="Это ID из внешнего API")
    telegram_user: models.ForeignKey[TelegramUser] = models.ForeignKey(
        TelegramUser,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name="Покупатель"
    )
    amount_fiat: models.DecimalField[Decimal] = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Сумма", help_text="Со скидкой (если есть)")
    amount_stars: models.IntegerField[int] = models.IntegerField(verbose_name="Звёзды")
    target_username: models.CharField[str] = models.CharField(max_length=255, blank=True, default=TARGET_SELF, verbose_name="Кому")
    status: models.CharField[str] = models.CharField(max_length=20, choices=TransactionStatus.to_choices(), default=TransactionStatus.PENDING, verbose_name="Статус")
    message_id: models.IntegerField[int] = models.IntegerField(default=-1, blank=True, verbose_name="ID сообщения заказа", help_text="Нужно для вебхука")
    pay_url: models.CharField[str] = models.CharField(max_length=255, default="dummy.pay.link", verbose_name="URL оплаты", help_text="Приходит из платёжной системы")
    created_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    expires_at: models.DateTimeField[datetime | None] = models.DateTimeField(null=True, blank=True, verbose_name="Истекает")
    updated_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now=True, verbose_name="Последнее обновление")

    @override
    def __str__(self):
        return f"Транзакция #{self.id} ({self.telegram_user})"

    @final
    class Meta:
        verbose_name = "Транзакция"
        verbose_name_plural = "Транзакции"
        indexes = [
            models.Index(fields=["status", "created_at"], name="transaction_status_created"),
        ]


@final
class TransactionMetadata(models.Model):
    transaction = models.OneToOneField(
        Transaction,
        on_delete=models.CASCADE,
        related_name="metadata_info",
        verbose_name="Транзакция"
    )
    type = models.CharField(max_length=50, choices=TransactionType.to_choices(), verbose_name="Тип")
    payment_method = models.CharField(max_length=50, verbose_name="Способ оплаты")
    promo_id = models.BigIntegerField(null=True, blank=True, verbose_name="ID промокода")
    promo_name = models.CharField(max_length=50, blank=True, verbose_name="Имя промокода")
    promo_discount = models.DecimalField(
        null=True, blank=True,
        decimal_places=2,
        max_digits=5,
        verbose_name="Скидка промокода %",
        help_text="Указывается в %, например, 5.00 (не 0.05)"
    )
    payload: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True, verbose_name="Доп. данные (JSON)")

    @override
    def __str__(self):
        return f"Метаданные для {self.transaction}"

    @final
    class Meta:
        verbose_name = "Метаданные транзакции"
        verbose_name_plural = "Метаданные транзакций"


class MonthlyProfit(Transaction):
    @final
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        proxy = True
        verbose_name = "Прибыль по месяцам"
        verbose_name_plural = "Прибыль по месяцам"


@final
class PaymentAPI(models.Model):
    if TYPE_CHECKING:
        methods: models.manager.RelatedManager["PaymentMethod"]  # pyright: ignore[reportUninitializedInstanceVariable]

    name = models.CharField(primary_key=True, max_length=50, verbose_name="Название API платёжных систем")

    @override
    def __str__(self):
        return self.name

    @final
    class Meta:
        verbose_name = "API платёжных систем"
        verbose_name_plural = "API платёжных систем"


@final
class PaymentMethod(models.Model):
    api = models.ForeignKey(
        PaymentAPI,
        on_delete=models.CASCADE,
        related_name="methods",
        verbose_name="API платёжных систем"
    )
    # TODO: добавить emoji-picker
    name = models.CharField(max_length=50, verbose_name="Название метода оплаты", help_text="Отображается в боте")
    external_id = models.CharField(max_length=255, verbose_name="ID метода оплаты", help_text="ID из внешнего API; Может быть числом или строкой")
    commission_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Комиссия %",
        help_text="Например, 1.00% (не 0.01)"
    )
    is_active = models.BooleanField(default=False, verbose_name="Активен?")

    @override
    def __str__(self):
        return f"{self.api.name} - {self.name} ({self.commission_percent}%)"

    @final
    class Meta:
        verbose_name = "Метод оплаты"
        verbose_name_plural = "Методы оплаты"

        constraints = [
            models.UniqueConstraint(
                fields=["api", "name"],
                name="unique_api_method_name"
            )
        ]

@final
class FragmentTransaction(models.Model):
    fragment_id = models.UUIDField(primary_key=True, verbose_name="ID Fragment")
    id_from_payment_api = models.UUIDField(verbose_name="ID из платёжного API")
    status = models.CharField(max_length=40, choices=FragmentStatus.to_choices(), default=FragmentStatus.CREATED, verbose_name="Статус FRAGMENT")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Последнее обновление")

    @override
    def __str__(self):
        return f"Транзакция Fragment #{self.fragment_id} (платёжное ID {self.id_from_payment_api})"

    @final
    class Meta:
        verbose_name = "Транзакция Fragment"
        verbose_name_plural = "Транзакции Fragment"
        indexes = [
            models.Index(fields=["id_from_payment_api"], name="fragment_tx_payment_id"),
        ]


@final
class GlobalSettings(SingletonModel):
    star_base_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.35"),
        verbose_name="Базовая цена одной звезды"
    )
    usd_base_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("80.00"),
        verbose_name="Базовый курс USD"
    )
    is_use_usd_rate = models.BooleanField(
        default=False,
        verbose_name="Учитывать курс доллара?"
    )
    maintenance_mode = models.BooleanField(
        default=False,
        verbose_name="Технический перерыв"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Последнее обновление")

    @classmethod
    async def aget_solo(cls):
        obj, _ = await cls.objects.aget_or_create(pk=cls.singleton_instance_id)
        return obj

    @override
    def __str__(self):
        return "Глобальные настройки"

    @final
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        verbose_name = "Глобальные настройки"


@final
class ExchangeRate(SingletonModel):
    usd_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("90.00"),
        verbose_name="Текущий курс USD"
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Последнее обновление")

    @classmethod
    async def aget_solo(cls):
        obj, _ = await cls.objects.aget_or_create(pk=cls.singleton_instance_id)
        return obj

    @override
    def __str__(self):
        return f"Курс доллара: {self.usd_rate}"

    @final
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        verbose_name = "Курс валют"


@final
class FragmentAPI(SingletonModel):
    # У fragment-api есть комиссия при переводах 0.5%, в данный момент она не учитывается при расчёте цен
    token = models.TextField(blank=True, help_text="Можно получить в Dashboard на fragment-api.com/dashboard")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Последнее обновление")

    @classmethod
    async def aget_solo(cls):
        obj, _ = await cls.objects.aget_or_create(pk=cls.singleton_instance_id)
        return obj

    @override
    def __str__(self):
        return "Токен для FragmentAPI"

    @final
    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        verbose_name = "Токен для FragmentAPI"


@final
class Broadcast(models.Model):
    if TYPE_CHECKING:
        pk: int | None
        id: int  # pyright: ignore[reportUninitializedInstanceVariable]

    name = models.CharField(blank=True, max_length=50, verbose_name="Название рассылки", help_text="Необязательно к заполнению")
    text = models.TextField(verbose_name="Текст сообщения", help_text="Для форматирования используется разметка HTML")
    media = models.FileField(upload_to="broadcasts/", blank=True, null=True, verbose_name="Медиа (Фото/Видео)")

    # Храним в виде JSON: [["Кнопка 1", "Кнопка 2"], ["Кнопка 3"]]
    button_texts: models.JSONField[list[list[str]] | None] = models.JSONField(blank=True, null=True, verbose_name="Тексты кнопок (JSON)", help_text='Формат (текст обязательно в двойных кавычках): null или список со списками названий, например, [["Строка1Столбец1", "Строка1Столбец2"], ["Строка2Столбец1"]]')
    button_urls: models.JSONField[list[list[str]] | None] = models.JSONField(blank=True, null=True, verbose_name="URL кнопок (JSON)", help_text='Формат такой же, как у текста кнопок (ссылки тоже должны быть в двойных кавычках)')

    telegram_file_id = models.CharField(max_length=255, blank=True, null=True, verbose_name="File ID в Telegram")
    preview_sent = models.BooleanField(default=False, verbose_name="Предпросмотр отправлен", help_text="Для массовой рассылки обязательно сначала отослать предпросмотр для кэширования файла")

    is_sent = models.BooleanField(default=False, verbose_name="Рассылка завершена")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    @final
    class Meta:
        verbose_name = "Рассылка"
        verbose_name_plural = "Рассылки"

    @override
    def __str__(self) -> str:
        if self.name:
            return self.name
        return f"Рассылка от {localtime(self.created_at).strftime('%d.%m.%Y %H:%M')}"

    def _check_update_fields(self, kwargs: SaveKwargs) -> None:
        """Модифицирует `update_fields` в исходном `kwargs` при необходимости."""

        update_fields = kwargs.get("update_fields")
        if update_fields is None or "media" in update_fields:
            self.preview_sent = False
            self.telegram_file_id = None

            if update_fields is not None:
                fields_set = set(update_fields)
                fields_set.update(["preview_sent", "telegram_file_id"])
                kwargs["update_fields"] = list(fields_set)

    @override
    def save(self, **kwargs: Unpack[SaveKwargs]):  # noqa  # pyright: ignore[reportIncompatibleMethodOverride]
        if self.pk:
            orig = Broadcast.objects.get(pk=self.pk)
            if orig.media != self.media:
                self._check_update_fields(kwargs)

        super().save(**kwargs)

    @override
    async def asave(self, **kwargs: Unpack[SaveKwargs]):  # noqa  # pyright: ignore[reportIncompatibleMethodOverride]
        if self.pk:
            orig = await Broadcast.objects.aget(pk=self.pk)
            if orig.media != self.media:
                self._check_update_fields(kwargs)

        await super().asave(**kwargs)


# TODO: можно сделать таблицу для file_id изображений сообщений, чтобы оптимизировать трафик (разобраться вместе с Gemini)
