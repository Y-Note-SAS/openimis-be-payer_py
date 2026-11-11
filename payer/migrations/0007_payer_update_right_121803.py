"""
The payer update right is 121803 (the 1218xx series: 121801 search, 121802 add,
121804 delete); DEFAULT_CFG named 122103, the medical items update right, so a role
holding "update medical items" could update payers and add fundings. 0006 granted that
id to the scheme administrator for the payers: grant it 121803 as well. The 122103
grant is left alone: it is a medical items right the role may legitimately hold.
"""
import logging

from django.db import migrations

logger = logging.getLogger(__name__)

SCHEME_ADMIN_ROLE_IS_SYSTEM = 32
PAYER_UPDATE_RIGHT_ID = 121803


def add_right(apps, schema_editor):
    Role = apps.get_model("core", "Role")
    RoleRight = apps.get_model("core", "RoleRight")
    role = Role.objects.filter(is_system=SCHEME_ADMIN_ROLE_IS_SYSTEM, validity_to__isnull=True).first()
    if role is None:
        logger.warning("No system role %s to grant the payer update right to", SCHEME_ADMIN_ROLE_IS_SYSTEM)
        return
    if not RoleRight.objects.filter(role=role, right_id=PAYER_UPDATE_RIGHT_ID, validity_to__isnull=True).exists():
        RoleRight.objects.create(role=role, right_id=PAYER_UPDATE_RIGHT_ID)


def remove_right(apps, schema_editor):
    RoleRight = apps.get_model("core", "RoleRight")
    RoleRight.objects.filter(
        role__is_system=SCHEME_ADMIN_ROLE_IS_SYSTEM, right_id=PAYER_UPDATE_RIGHT_ID, validity_to__isnull=True
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('payer', '0006_add_search_authority_for_eo'),
    ]

    operations = [
        migrations.RunPython(add_right, remove_right),
    ]
