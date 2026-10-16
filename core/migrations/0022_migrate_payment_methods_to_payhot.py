from django.db import migrations

PAYHOT = "PayHot"

PAYHOT_METHODS = (
    ("СБП", "sbp"),
    ("Карта", "card"),
    ("Криптовалюта", "crypto"),
)


def migrate_to_payhot(apps, schema_editor):
    payment_api = apps.get_model("core", "paymentapi")
    payment_method = apps.get_model("core", "paymentmethod")

    api, _ = payment_api.objects.get_or_create(name=PAYHOT)
    _ = payment_method.objects.exclude(api=api).update(is_active=False)
    for name, external_id in PAYHOT_METHODS:
        _ = payment_method.objects.update_or_create(
            api=api, external_id=external_id, defaults={"name": name, "is_active": True}
        )


def remove_payhot(apps, schema_editor):
    payment_api = apps.get_model("core", "paymentapi")
    _ = payment_api.objects.filter(name=PAYHOT).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_add_poll_indexes"),
    ]

    operations = [
        migrations.RunPython(migrate_to_payhot, remove_payhot),
    ]
