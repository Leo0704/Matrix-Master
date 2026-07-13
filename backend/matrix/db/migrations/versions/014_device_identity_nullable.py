"""devices 表的 4 个手填字段改 nullable，APK 上线后自动回填

P2-3 简化添加设备表单：旧设计让用户手填 model/android_version/apk_version/tailnet_ip，
但 ``/devices/{id}/pair`` 收到 APK 自报（model / os_version / tailscale_ip / apk_version）
后从没写回 DB——用户填的字段是被 APK 上线后实际值覆盖的冗余设计。

这一刀：把这 3 个 string 列改成 nullable，让主控可以先创设备行（status=pending），
等 APK 配对时把真实身份写回。

``tailnet_ip`` 已经是 nullable（迁移 001 时期就是 Optional），跳过。

降级时把 NULL 行填占位（'unknown' / '0'），避免 NOT NULL 重新启用后报错。
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "014_device_identity_nullable"
down_revision = "013_goal_phase_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE devices ALTER COLUMN model            DROP NOT NULL;")
    op.execute("ALTER TABLE devices ALTER COLUMN android_version DROP NOT NULL;")
    op.execute("ALTER TABLE devices ALTER COLUMN apk_version      DROP NOT NULL;")


def downgrade() -> None:
    # 用占位防止 SET NOT NULL 时撞 NULL 行
    op.execute(
        "UPDATE devices SET model = COALESCE(model, 'unknown') "
        "WHERE model IS NULL;"
    )
    op.execute(
        "UPDATE devices SET android_version = COALESCE(android_version, '0') "
        "WHERE android_version IS NULL;"
    )
    op.execute(
        "UPDATE devices SET apk_version = COALESCE(apk_version, 'unknown') "
        "WHERE apk_version IS NULL;"
    )
    op.execute("ALTER TABLE devices ALTER COLUMN model            SET NOT NULL;")
    op.execute("ALTER TABLE devices ALTER COLUMN android_version SET NOT NULL;")
    op.execute("ALTER TABLE devices ALTER COLUMN apk_version      SET NOT NULL;")
