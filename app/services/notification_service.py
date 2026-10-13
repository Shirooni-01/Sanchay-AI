"""Transactional notification service for Sanchay AI (Phase 7).

Manages user-scoped in-app notifications, event deduplication,
batch inventory alerts, and inter-store transfer lifecycle alerts.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import logging
from typing import Any

from sqlalchemy import or_

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch
from app.models.notification import Notification, VALID_NOTIFICATION_TYPES
from app.models.organization import Organization, Shop, UserShopAssignment
from app.models.transfer import TransferRequest
from app.models.user import User
from app.utils.errors import AuthorizationError, ResourceNotFoundError

logger = logging.getLogger(__name__)


class NotificationService:
    """Service layer managing in-app notifications and event alerts."""

    @staticmethod
    def create_notification(
        recipient_user_id: int,
        organization_id: int,
        notification_type: str,
        title: str,
        message: str,
        shop_id: int | None = None,
        severity: str = "INFO",
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
        dedup_key: str | None = None,
        auto_commit: bool = True,
    ) -> Notification:
        """Create a single user notification, deduplicating if dedup_key is supplied.

        If dedup_key is supplied and an identical unread/existing notification exists
        for recipient_user_id, returns the existing record without creating duplicates.
        """
        clean_type = notification_type.strip().upper()
        if clean_type not in VALID_NOTIFICATION_TYPES:
            clean_type = "SYSTEM_ALERT"

        clean_severity = severity.strip().upper() if severity else "INFO"
        if clean_severity not in ("INFO", "WARNING", "CRITICAL"):
            clean_severity = "INFO"

        if dedup_key:
            existing = (
                db.session.query(Notification)
                .filter(
                    Notification.recipient_user_id == int(recipient_user_id),
                    Notification.dedup_key == dedup_key,
                )
                .first()
            )
            if existing is not None:
                existing._is_new = False
                return existing

        notif = Notification(
            recipient_user_id=int(recipient_user_id),
            organization_id=int(organization_id),
            shop_id=int(shop_id) if shop_id is not None else None,
            notification_type=clean_type,
            severity=clean_severity,
            title=title.strip()[:160],
            message=message.strip(),
            related_entity_type=related_entity_type,
            related_entity_id=int(related_entity_id) if related_entity_id is not None else None,
            dedup_key=dedup_key,
            created_at=datetime.now(timezone.utc),
        )
        notif._is_new = True
        db.session.add(notif)
        if auto_commit:
            db.session.commit()
        return notif

    @staticmethod
    def get_shop_recipient_user_ids(
        shop_id: int,
        roles: set[str] | None = None,
    ) -> list[int]:
        """Find active user IDs authorized for a shop, optionally filtered by role."""
        query = (
            db.session.query(UserShopAssignment.user_id)
            .join(User, UserShopAssignment.user_id == User.id)
            .filter(
                UserShopAssignment.shop_id == int(shop_id),
                UserShopAssignment.is_active.is_(True),
                User.is_active.is_(True),
            )
        )
        if roles:
            query = query.filter(UserShopAssignment.assignment_role.in_(roles))

        user_ids = {row[0] for row in query.all()}

        # Always include shop owner if shop has owner_user_id and roles allow SHOP_OWNER
        shop = db.session.get(Shop, int(shop_id))
        if shop and getattr(shop, "owner_user_id", None):
            if roles is None or "SHOP_OWNER" in roles:
                owner = db.session.get(User, shop.owner_user_id)
                if owner and owner.is_active:
                    user_ids.add(owner.id)

        return sorted(list(user_ids))

    @staticmethod
    def notify_shop_users(
        shop_id: int,
        notification_type: str,
        title: str,
        message: str,
        roles: set[str] | None = None,
        severity: str = "INFO",
        related_entity_type: str | None = None,
        related_entity_id: int | None = None,
        dedup_key_prefix: str | None = None,
        auto_commit: bool = True,
    ) -> list[Notification]:
        """Broadcast an event notification to all authorized active shop users."""
        shop = db.session.get(Shop, int(shop_id))
        if not shop:
            return []

        recipient_user_ids = NotificationService.get_shop_recipient_user_ids(
            shop_id=shop.id,
            roles=roles,
        )

        notifications: list[Notification] = []
        for user_id in recipient_user_ids:
            user_dedup_key = (
                f"{dedup_key_prefix}:user:{user_id}"
                if dedup_key_prefix
                else None
            )
            n = NotificationService.create_notification(
                recipient_user_id=user_id,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                notification_type=notification_type,
                title=title,
                message=message,
                severity=severity,
                related_entity_type=related_entity_type,
                related_entity_id=related_entity_id,
                dedup_key=user_dedup_key,
                auto_commit=False,
            )
            if getattr(n, "_is_new", True):
                notifications.append(n)

        if auto_commit and notifications:
            db.session.commit()

        return notifications

    @staticmethod
    def list_user_notifications(
        user: User,
        is_read: bool | None = None,
        notification_type: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """List paginated notifications exclusively belonging to the actor user."""
        page = max(1, int(page or 1))
        per_page = max(1, min(100, int(per_page or 20)))

        query = db.session.query(Notification).filter(
            Notification.recipient_user_id == user.id
        )

        if is_read is not None:
            query = query.filter(Notification.is_read == bool(is_read))

        if notification_type and notification_type.strip():
            query = query.filter(
                Notification.notification_type == notification_type.strip().upper()
            )

        total_items = query.count()
        total_pages = max(1, (total_items + per_page - 1) // per_page)

        unread_count = (
            db.session.query(Notification)
            .filter(
                Notification.recipient_user_id == user.id,
                Notification.is_read.is_(False),
            )
            .count()
        )

        rows = (
            query.order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        return {
            "items": [n.to_dict() for n in rows],
            "unread_count": unread_count,
            "meta": {
                "page": page,
                "per_page": per_page,
                "total_items": total_items,
                "total_pages": total_pages,
                "unread_count": unread_count,
            },
        }

    @staticmethod
    def get_unread_count(user: User) -> int:
        """Return the unread notification count for the authenticated user."""
        return (
            db.session.query(Notification)
            .filter(
                Notification.recipient_user_id == user.id,
                Notification.is_read.is_(False),
            )
            .count()
        )

    @staticmethod
    def mark_as_read(user: User, notification_id: int) -> Notification:
        """Mark a single notification as read, validating recipient ownership."""
        notif = db.session.get(Notification, int(notification_id))
        if not notif:
            raise ResourceNotFoundError(
                message=f"Notification #{notification_id} was not found.",
                code="NOTIFICATION_NOT_FOUND",
            )

        if notif.recipient_user_id != user.id:
            raise AuthorizationError(
                message="Cannot mark another user's notification as read.",
                code="FORBIDDEN_NOTIFICATION_ACCESS",
            )

        if not notif.is_read:
            notif.is_read = True
            notif.read_at = datetime.now(timezone.utc)
            db.session.commit()

        return notif

    @staticmethod
    def mark_all_as_read(user: User) -> int:
        """Mark all unread notifications for the user as read."""
        now = datetime.now(timezone.utc)
        count = (
            db.session.query(Notification)
            .filter(
                Notification.recipient_user_id == user.id,
                Notification.is_read.is_(False),
            )
            .update(
                {"is_read": True, "read_at": now},
                synchronize_session="fetch",
            )
        )
        db.session.commit()
        return count

    @staticmethod
    def check_and_notify_inventory_alerts(
        shop_id: int,
        ref_date: date | None = None,
        auto_commit: bool = True,
    ) -> list[Notification]:
        """Evaluate inventory alerts for a shop and create deduplicated daily notifications."""
        from app.services.inventory_service import InventoryService

        shop = db.session.get(Shop, int(shop_id))
        if not shop or shop.status != "ACTIVE":
            return []

        today = ref_date or date.today()
        today_str = today.isoformat()

        # Sweep expired batches
        InventoryService.sweep_expired_batches(shop_id=shop.id, ref_date=today)

        shop_products = (
            db.session.query(ShopProduct)
            .join(Product, ShopProduct.product_id == Product.id)
            .filter(ShopProduct.shop_id == shop.id, Product.is_active.is_(True))
            .all()
        )

        created_notifs: list[Notification] = []

        for sp in shop_products:
            batches = (
                db.session.query(InventoryBatch)
                .filter(
                    InventoryBatch.shop_id == shop.id,
                    InventoryBatch.product_id == sp.product_id,
                )
                .order_by(
                    InventoryBatch.expiry_date.asc().nulls_last(),
                    InventoryBatch.received_at.asc(),
                    InventoryBatch.id.asc(),
                )
                .all()
            )

            summary = InventoryService.compute_shop_product_stock_summary(
                shop_product=sp,
                ref_date=today,
                batches=batches,
            )

            prod_name = sp.product.name if sp.product else f"Product #{sp.product_id}"
            sku = sp.product.sku if sp.product else ""

            # 1. Low stock alert
            if summary["is_low_stock"]:
                is_out = summary["is_out_of_stock"]
                severity = "CRITICAL" if is_out else "WARNING"
                title = f"{'Out of Stock' if is_out else 'Low Stock'}: {prod_name}"
                message = (
                    f"Product '{prod_name}' ({sku}) has {summary['sellable_qty']} sellable units "
                    f"remaining, which is below the threshold of {sp.low_stock_threshold}."
                )
                dedup_prefix = f"LOW_STOCK:{shop.id}:{sp.product_id}:{today_str}"
                notifs = NotificationService.notify_shop_users(
                    shop_id=shop.id,
                    notification_type="LOW_STOCK",
                    title=title,
                    message=message,
                    severity=severity,
                    related_entity_type="product",
                    related_entity_id=sp.product_id,
                    dedup_key_prefix=dedup_prefix,
                    auto_commit=False,
                )
                created_notifs.extend(notifs)

            # 2. Near expiry / Expired alerts per batch
            warn_days = int(sp.near_expiry_days or shop.near_expiry_days_threshold or 15)
            for b in batches:
                if b.status != "ACTIVE":
                    continue
                if b.expiry_date is None:
                    continue

                days_left = (b.expiry_date - today).days

                # Quarantined expired batch
                if days_left < 0 or int(b.expired_qty or 0) > 0:
                    dedup_prefix = f"EXPIRED_BATCH:{shop.id}:{b.id}:{today_str}"
                    title = f"Expired Batch Quarantined: {b.batch_code}"
                    message = (
                        f"Batch '{b.batch_code}' of '{prod_name}' expired on {b.expiry_date.isoformat()} "
                        f"and was quarantined to protect customers."
                    )
                    notifs = NotificationService.notify_shop_users(
                        shop_id=shop.id,
                        notification_type="NEAR_EXPIRY",
                        title=title,
                        message=message,
                        severity="CRITICAL",
                        related_entity_type="batch",
                        related_entity_id=b.id,
                        dedup_key_prefix=dedup_prefix,
                        auto_commit=False,
                    )
                    created_notifs.extend(notifs)

                # Near expiry batch
                elif 0 <= days_left <= warn_days and int(b.available_qty or 0) > 0:
                    dedup_prefix = f"NEAR_EXPIRY:{shop.id}:{b.id}:{today_str}"
                    title = f"Near Expiry: Batch {b.batch_code}"
                    severity = "CRITICAL" if days_left <= 2 else "WARNING"
                    message = (
                        f"Batch '{b.batch_code}' of '{prod_name}' expires in {days_left} days "
                        f"({b.expiry_date.isoformat()}). {b.available_qty} units available."
                    )
                    notifs = NotificationService.notify_shop_users(
                        shop_id=shop.id,
                        notification_type="NEAR_EXPIRY",
                        title=title,
                        message=message,
                        severity=severity,
                        related_entity_type="batch",
                        related_entity_id=b.id,
                        dedup_key_prefix=dedup_prefix,
                        auto_commit=False,
                    )
                    created_notifs.extend(notifs)

        if auto_commit and created_notifs:
            db.session.commit()

        return created_notifs

    @staticmethod
    def check_and_notify_stock_risk_alerts(
        shop_id: int,
        ref_date: date | None = None,
        auto_commit: bool = True,
    ) -> list[Notification]:
        """Evaluate Phase 5 deterministic surplus and risk insights and create notifications."""
        from app.services.surplus_service import StockAnalysisService

        shop = db.session.get(Shop, int(shop_id))
        if not shop or shop.status != "ACTIVE":
            return []

        today = ref_date or date.today()
        today_str = today.isoformat()

        analysis = StockAnalysisService.analyze_shop(
            shop_id=shop.id,
            ref_date=today,
        )

        created: list[Notification] = []

        # 1. Critical expiry risk batches
        for batch_risk in analysis.get("expiry_risk_batches", []):
            if batch_risk.get("risk_level") == "CRITICAL":
                batch_id = batch_risk.get("batch_id")
                batch_code = batch_risk.get("batch_code")
                prod_name = batch_risk.get("product_name")
                proj_unsold = batch_risk.get("projected_unsold_units", 0)
                dedup_prefix = f"EXPIRY_RISK_CRITICAL:{shop.id}:{batch_id}:{today_str}"
                title = f"Critical Expiry Risk: {batch_code}"
                message = (
                    f"Batch '{batch_code}' of '{prod_name}' is projected to have "
                    f"{proj_unsold} unsold units expire at current sales velocity."
                )
                notifs = NotificationService.notify_shop_users(
                    shop_id=shop.id,
                    notification_type="EXPIRY_RISK_CRITICAL",
                    title=title,
                    message=message,
                    severity="CRITICAL",
                    related_entity_type="batch",
                    related_entity_id=batch_id,
                    dedup_key_prefix=dedup_prefix,
                    auto_commit=False,
                )
                created.extend(notifs)

        # 2. Surplus candidates detected
        for surplus in analysis.get("surplus_candidates", []):
            batch_id = surplus.get("batch_id")
            batch_code = surplus.get("batch_code")
            prod_name = surplus.get("product_name")
            surplus_qty = surplus.get("potential_surplus_qty", 0)
            if surplus_qty > 0:
                dedup_prefix = f"SURPLUS_DETECTED:{shop.id}:{batch_id}:{today_str}"
                title = f"Transferable Surplus Detected: {prod_name}"
                message = (
                    f"{surplus_qty} units of '{prod_name}' (Batch {batch_code}) identified as "
                    f"surplus stock available for inter-store transfer."
                )
                notifs = NotificationService.notify_shop_users(
                    shop_id=shop.id,
                    notification_type="SURPLUS_DETECTED",
                    title=title,
                    message=message,
                    roles={"SHOP_OWNER"},
                    severity="INFO",
                    related_entity_type="batch",
                    related_entity_id=batch_id,
                    dedup_key_prefix=dedup_prefix,
                    auto_commit=False,
                )
                created.extend(notifs)

        if auto_commit and created:
            db.session.commit()

        return created
