"""Product Catalog & Shop-Product Configuration Service (Phase 3).

Enforces:
- Manual product entry only (no barcode/QR/OCR scanning — RULE-05, RULE-06).
- Two-tier catalog model (ADR-006): Organization-scoped canonical Product + ShopProduct configuration.
- Strict organization and shop isolation (BR-013, BR-014).
- Soft deactivation preserving historical batches and stock movements (FR-025).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch, StockMovement
from app.models.organization import Shop
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.inventory_service import InventoryService, get_business_today
from app.utils.decorators import (
    verify_organization_access,
    verify_shop_access,
)
from app.utils.errors import (
    AuthorizationError,
    DatabaseConflictError,
    DomainError,
    DuplicateResourceError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.utils.validators import (
    validate_pagination_params,
    validate_product_create_input,
    validate_product_update_input,
)


class CatalogService:
    """Service managing canonical organization products and shop-scoped product catalogs."""

    @staticmethod
    def _ensure_catalog_write_role(actor: User, shop_id: int | None = None) -> None:
        """Ensure actor is SUPER_ADMIN or SHOP_OWNER before mutating catalog records."""
        if actor.role not in {"SUPER_ADMIN", "SHOP_OWNER"}:
            AuditService.record(
                action="INSUFFICIENT_ROLE_PERMISSION",
                entity_type="PRODUCT",
                actor=actor,
                organization_id=actor.organization_id,
                shop_id=shop_id,
                after_state={"reason": f"role_{actor.role}_cannot_mutate_catalog"},
            )
            db.session.commit()
            raise AuthorizationError(
                message="Only Super Admins and Shop Owners can create or edit products.",
                code="INSUFFICIENT_ROLE_PERMISSION",
            )

    @staticmethod
    def create_organization_product(
        actor: User,
        payload: dict[str, Any],
        shop_id: int | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Product:
        """Create a canonical Product within the actor's active Organization."""
        org = verify_organization_access(user=actor, require_active=True)
        CatalogService._ensure_catalog_write_role(actor=actor, shop_id=shop_id)

        cleaned = validate_product_create_input(
            payload=payload,
            default_reorder_threshold=org.low_stock_default_threshold,
            default_near_expiry_days=org.near_expiry_days_threshold,
        )

        try:
            existing = (
                db.session.query(Product)
                .filter(
                    Product.organization_id == org.id,
                    func.upper(Product.sku) == cleaned["sku"],
                )
                .first()
            )
            if existing is not None:
                raise DuplicateResourceError(
                    message=f"A product with SKU '{cleaned['sku']}' already exists in this organization.",
                    details={"sku": cleaned["sku"], "organization_id": org.id},
                )

            product = Product(
                organization_id=org.id,
                sku=cleaned["sku"],
                name=cleaned["name"],
                category=cleaned["category"],
                unit=cleaned["unit"],
                description=cleaned["description"],
                is_perishable=cleaned["is_perishable"],
                default_purchase_price=cleaned["default_purchase_price"],
                default_selling_price=cleaned["default_selling_price"],
                reorder_threshold=cleaned["reorder_threshold"],
                is_active=cleaned["is_active"],
                created_by_user_id=actor.id,
            )
            db.session.add(product)
            db.session.flush()

            if shop_id is not None:
                shop, _ = verify_shop_access(
                    user=actor,
                    shop_id=int(shop_id),
                    allow_inactive=False,
                    required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
                )
                shop_product = ShopProduct(
                    shop_id=shop.id,
                    product_id=product.id,
                    purchase_price=cleaned["purchase_price"],
                    selling_price=cleaned["selling_price"],
                    low_stock_threshold=cleaned["low_stock_threshold"],
                    near_expiry_days=cleaned["near_expiry_days"],
                    surplus_threshold=cleaned["surplus_threshold"],
                    is_active=cleaned["is_active"],
                )
                db.session.add(shop_product)
                db.session.flush()

            AuditService.record(
                action="PRODUCT_CREATED",
                entity_type="PRODUCT",
                entity_id=product.id,
                actor=actor,
                organization_id=org.id,
                shop_id=shop_id,
                after_state=product.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return product

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="A product with this SKU already exists in your organization.",
                details={"sku": cleaned.get("sku")},
            ) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while creating product.",
                code="DATABASE_LOCK_OR_CONNECTION_ERROR",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def create_or_enroll_shop_product(
        actor: User,
        shop_id: int,
        payload: dict[str, Any],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> ShopProduct:
        """Create a new product in the shop catalog or enroll an existing organization product (API-014)."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=False,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
        )

        cleaned = validate_product_create_input(
            payload=payload,
            default_reorder_threshold=shop.low_stock_default_threshold,
            default_near_expiry_days=shop.near_expiry_days_threshold,
        )

        try:
            # Mode 1: Enrolling an existing organization product by product_id
            if "product_id" in cleaned:
                product = db.session.get(Product, int(cleaned["product_id"]))
                if product is None:
                    raise ResourceNotFoundError(
                        message=f"Product #{cleaned['product_id']} was not found."
                    )
                if product.organization_id != shop.organization_id:
                    AuditService.record(
                        action="UNAUTHORIZED_ORG_PRODUCT_ACCESS",
                        entity_type="PRODUCT",
                        entity_id=product.id,
                        actor=actor,
                        organization_id=actor.organization_id,
                        shop_id=shop.id,
                        after_state={"product_org_id": product.organization_id},
                        ip_address=ip_address,
                        user_agent=user_agent,
                    )
                    db.session.commit()
                    raise AuthorizationError(
                        message="Cannot enroll a product belonging to another organization.",
                        code="UNAUTHORIZED_ORG_ACCESS",
                    )

                existing_sp = (
                    db.session.query(ShopProduct)
                    .filter(
                        ShopProduct.shop_id == shop.id,
                        ShopProduct.product_id == product.id,
                    )
                    .first()
                )
                if existing_sp is not None:
                    raise DuplicateResourceError(
                        message=f"Product '{product.sku}' is already enrolled in shop '{shop.name}'.",
                        details={"shop_id": shop.id, "product_id": product.id, "sku": product.sku},
                    )

                shop_product = ShopProduct(
                    shop_id=shop.id,
                    product_id=product.id,
                    purchase_price=(
                        cleaned["purchase_price"]
                        if cleaned["purchase_price"] is not None
                        else product.default_purchase_price
                    ),
                    selling_price=(
                        cleaned["selling_price"]
                        if cleaned["selling_price"] is not None
                        else product.default_selling_price
                    ),
                    low_stock_threshold=cleaned["low_stock_threshold"],
                    near_expiry_days=cleaned["near_expiry_days"],
                    surplus_threshold=cleaned["surplus_threshold"],
                    is_active=cleaned["is_active"],
                )
                db.session.add(shop_product)
                db.session.flush()

                AuditService.record(
                    action="SHOP_PRODUCT_ENROLLED",
                    entity_type="SHOP_PRODUCT",
                    entity_id=shop_product.id,
                    actor=actor,
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    after_state=shop_product.to_dict(),
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                db.session.commit()
                return shop_product

            # Mode 2: Creating by SKU
            sku = cleaned["sku"]
            existing_product = (
                db.session.query(Product)
                .filter(
                    Product.organization_id == shop.organization_id,
                    func.upper(Product.sku) == sku,
                )
                .first()
            )

            if existing_product is not None:
                if not cleaned.get("link_existing", False):
                    raise DuplicateResourceError(
                        message=(
                            f"A product with SKU '{sku}' already exists in your organization. "
                            "Use a unique SKU or link the existing product."
                        ),
                        details={"sku": sku, "existing_product_id": existing_product.id},
                    )

                existing_sp = (
                    db.session.query(ShopProduct)
                    .filter(
                        ShopProduct.shop_id == shop.id,
                        ShopProduct.product_id == existing_product.id,
                    )
                    .first()
                )
                if existing_sp is not None:
                    raise DuplicateResourceError(
                        message=f"Product SKU '{sku}' is already registered in shop '{shop.name}'.",
                        details={"sku": sku, "shop_id": shop.id},
                    )

                shop_product = ShopProduct(
                    shop_id=shop.id,
                    product_id=existing_product.id,
                    purchase_price=cleaned["purchase_price"],
                    selling_price=cleaned["selling_price"],
                    low_stock_threshold=cleaned["low_stock_threshold"],
                    near_expiry_days=cleaned["near_expiry_days"],
                    surplus_threshold=cleaned["surplus_threshold"],
                    is_active=cleaned["is_active"],
                )
                db.session.add(shop_product)
                db.session.flush()

                AuditService.record(
                    action="SHOP_PRODUCT_ENROLLED",
                    entity_type="SHOP_PRODUCT",
                    entity_id=shop_product.id,
                    actor=actor,
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    after_state=shop_product.to_dict(),
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                db.session.commit()
                return shop_product

            # Create both canonical Product and ShopProduct atomically
            product = Product(
                organization_id=shop.organization_id,
                sku=sku,
                name=cleaned["name"],
                category=cleaned["category"],
                unit=cleaned["unit"],
                description=cleaned["description"],
                is_perishable=cleaned["is_perishable"],
                default_purchase_price=cleaned["default_purchase_price"],
                default_selling_price=cleaned["default_selling_price"],
                reorder_threshold=cleaned["reorder_threshold"],
                is_active=cleaned["is_active"],
                created_by_user_id=actor.id,
            )
            db.session.add(product)
            db.session.flush()

            shop_product = ShopProduct(
                shop_id=shop.id,
                product_id=product.id,
                purchase_price=cleaned["purchase_price"],
                selling_price=cleaned["selling_price"],
                low_stock_threshold=cleaned["low_stock_threshold"],
                near_expiry_days=cleaned["near_expiry_days"],
                surplus_threshold=cleaned["surplus_threshold"],
                is_active=cleaned["is_active"],
            )
            db.session.add(shop_product)
            db.session.flush()

            AuditService.record(
                action="PRODUCT_CREATED",
                entity_type="PRODUCT",
                entity_id=product.id,
                actor=actor,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                after_state=shop_product.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return shop_product

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="A product with this SKU already exists in the catalog.",
                details={"sku": cleaned.get("sku")},
            ) from exc
        except OperationalError as exc:
            db.session.rollback()
            raise DatabaseConflictError(
                message="Database error while creating shop product.",
                code="DATABASE_LOCK_OR_CONNECTION_ERROR",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def resolve_shop_product(shop_id: int, identifier: int) -> ShopProduct:
        """Resolve a ShopProduct belonging to shop_id by shop_product.id or product.id."""
        target = int(identifier)
        sp = (
            db.session.query(ShopProduct)
            .filter(
                ShopProduct.shop_id == int(shop_id),
                ShopProduct.id == target,
            )
            .first()
        )
        if sp is not None:
            return sp

        sp_by_prod = (
            db.session.query(ShopProduct)
            .filter(
                ShopProduct.shop_id == int(shop_id),
                ShopProduct.product_id == target,
            )
            .first()
        )
        if sp_by_prod is not None:
            return sp_by_prod

        # Check if the identifier belongs to another shop to give proper 403 vs 404
        other_sp = db.session.get(ShopProduct, target)
        if other_sp is not None and other_sp.shop_id != int(shop_id):
            raise AuthorizationError(
                message="Requested shop product belongs to a different shop.",
                code="UNAUTHORIZED_SHOP_ACCESS",
            )

        raise ResourceNotFoundError(
            message=f"Product #{identifier} was not found in this shop's catalog."
        )

    @staticmethod
    def update_organization_product(
        actor: User,
        product_id: int,
        payload: dict[str, Any],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Product:
        """Update canonical Product fields or deactivate it (preserving historical references)."""
        org = verify_organization_access(user=actor, require_active=True)
        CatalogService._ensure_catalog_write_role(actor=actor)

        product = db.session.get(Product, int(product_id))
        if product is None:
            raise ResourceNotFoundError(message=f"Product #{product_id} was not found.")
        if product.organization_id != org.id:
            AuditService.record(
                action="UNAUTHORIZED_ORG_PRODUCT_ACCESS",
                entity_type="PRODUCT",
                entity_id=product.id,
                actor=actor,
                organization_id=org.id,
                after_state={"product_org_id": product.organization_id},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            raise AuthorizationError(
                message="Cannot modify a product belonging to another organization.",
                code="UNAUTHORIZED_ORG_ACCESS",
            )

        cleaned = validate_product_update_input(payload)

        try:
            before_state = product.to_dict()

            if "sku" in cleaned and cleaned["sku"] != product.sku:
                dup = (
                    db.session.query(Product)
                    .filter(
                        Product.organization_id == org.id,
                        func.upper(Product.sku) == cleaned["sku"],
                        Product.id != product.id,
                    )
                    .first()
                )
                if dup is not None:
                    raise DuplicateResourceError(
                        message=f"Another product in this organization already uses SKU '{cleaned['sku']}'.",
                        details={"sku": cleaned["sku"]},
                    )
                product.sku = cleaned["sku"]

            if cleaned.get("is_active") is False and product.is_active:
                reserved_sum = (
                    db.session.query(
                        func.coalesce(func.sum(InventoryBatch.reserved_qty), 0)
                    )
                    .filter(InventoryBatch.product_id == product.id)
                    .scalar()
                )
                if int(reserved_sum or 0) > 0:
                    raise ResourceConflictError(
                        message=(
                            f"Cannot deactivate product '{product.sku}' while {reserved_sum} "
                            "units are reserved in active transfers."
                        ),
                        code="ACTIVE_RESERVATIONS_EXIST",
                    )

            for field in (
                "name",
                "category",
                "unit",
                "description",
                "is_perishable",
                "default_purchase_price",
                "default_selling_price",
                "reorder_threshold",
                "is_active",
            ):
                if field in cleaned:
                    setattr(product, field, cleaned[field])

            db.session.flush()

            AuditService.record(
                action=(
                    "PRODUCT_DEACTIVATED"
                    if before_state["is_active"] and not product.is_active
                    else "PRODUCT_UPDATED"
                ),
                entity_type="PRODUCT",
                entity_id=product.id,
                actor=actor,
                organization_id=org.id,
                before_state=before_state,
                after_state=product.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return product

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="SKU conflict while updating product.",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def update_shop_product(
        actor: User,
        shop_id: int,
        identifier: int,
        payload: dict[str, Any],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> ShopProduct:
        """Update shop product prices, thresholds, active status, and optional master product fields (API-015)."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=False,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER"},
        )
        shop_product = CatalogService.resolve_shop_product(shop.id, identifier)
        product = shop_product.product or db.session.get(Product, shop_product.product_id)

        cleaned = validate_product_update_input(payload)

        try:
            before_state = shop_product.to_dict()

            if cleaned.get("is_active") is False and shop_product.is_active:
                reserved_sum = (
                    db.session.query(
                        func.coalesce(func.sum(InventoryBatch.reserved_qty), 0)
                    )
                    .filter(
                        InventoryBatch.shop_id == shop.id,
                        InventoryBatch.product_id == shop_product.product_id,
                    )
                    .scalar()
                )
                if int(reserved_sum or 0) > 0:
                    raise ResourceConflictError(
                        message=(
                            f"Cannot deactivate product in shop '{shop.name}' while "
                            f"{reserved_sum} units are reserved in active transfers."
                        ),
                        code="ACTIVE_RESERVATIONS_EXIST",
                    )

            # Update shop-level configuration fields
            if "purchase_price" in cleaned:
                shop_product.purchase_price = cleaned["purchase_price"]
            elif "default_purchase_price" in cleaned:
                shop_product.purchase_price = cleaned["default_purchase_price"]

            if "selling_price" in cleaned:
                shop_product.selling_price = cleaned["selling_price"]
            elif "default_selling_price" in cleaned:
                shop_product.selling_price = cleaned["default_selling_price"]

            for sp_field in (
                "low_stock_threshold",
                "near_expiry_days",
                "surplus_threshold",
                "is_active",
            ):
                if sp_field in cleaned:
                    setattr(shop_product, sp_field, cleaned[sp_field])

            # Update canonical product fields if included in payload
            if product is not None:
                if "sku" in cleaned and cleaned["sku"] != product.sku:
                    dup = (
                        db.session.query(Product)
                        .filter(
                            Product.organization_id == shop.organization_id,
                            func.upper(Product.sku) == cleaned["sku"],
                            Product.id != product.id,
                        )
                        .first()
                    )
                    if dup is not None:
                        raise DuplicateResourceError(
                            message=f"SKU '{cleaned['sku']}' is already used by another product in your organization.",
                            details={"sku": cleaned["sku"]},
                        )
                    product.sku = cleaned["sku"]

                for p_field in (
                    "name",
                    "category",
                    "unit",
                    "description",
                    "is_perishable",
                    "default_purchase_price",
                    "default_selling_price",
                    "reorder_threshold",
                ):
                    if p_field in cleaned:
                        setattr(product, p_field, cleaned[p_field])

            db.session.flush()

            AuditService.record(
                action=(
                    "SHOP_PRODUCT_DEACTIVATED"
                    if before_state["shop_product_is_active"] and not shop_product.is_active
                    else "SHOP_PRODUCT_UPDATED"
                ),
                entity_type="SHOP_PRODUCT",
                entity_id=shop_product.id,
                actor=actor,
                organization_id=shop.organization_id,
                shop_id=shop.id,
                before_state=before_state,
                after_state=shop_product.to_dict(),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.commit()
            return shop_product

        except DomainError:
            db.session.rollback()
            raise
        except IntegrityError as exc:
            db.session.rollback()
            raise DuplicateResourceError(
                message="Product update conflicted with an existing SKU.",
            ) from exc
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def prevent_destructive_product_delete(
        actor: User,
        product_id: int | None = None,
        shop_id: int | None = None,
    ) -> None:
        """Explicitly block destructive deletion of products (RULE-22 & Phase 3 Req 2)."""
        AuditService.record(
            action="DESTRUCTIVE_PRODUCT_DELETE_BLOCKED",
            entity_type="PRODUCT",
            entity_id=product_id,
            actor=actor,
            organization_id=actor.organization_id,
            shop_id=shop_id,
            after_state={"reason": "destructive_delete_prohibited"},
        )
        db.session.commit()
        raise ResourceConflictError(
            message=(
                "Products with catalog or transaction history cannot be destructively deleted. "
                "Deactivate the product (is_active=false) to preserve historical records."
            ),
            code="DESTRUCTIVE_DELETE_PROHIBITED",
        )

    @staticmethod
    def list_organization_products(
        actor: User,
        search_query: str | None = None,
        category: str | None = None,
        is_active: bool | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Return paginated canonical organization products for the actor's organization."""
        org = verify_organization_access(user=actor, require_active=False)
        page, per_page = validate_pagination_params(page, per_page)

        query = db.session.query(Product).filter(Product.organization_id == org.id)

        if search_query and search_query.strip():
            term = f"%{search_query.strip()}%"
            query = query.filter(
                or_(
                    Product.sku.ilike(term),
                    Product.name.ilike(term),
                    Product.category.ilike(term),
                    Product.description.ilike(term),
                )
            )

        if category and category.strip():
            query = query.filter(func.lower(Product.category) == category.strip().lower())

        if is_active is not None:
            query = query.filter(Product.is_active.is_(bool(is_active)))

        total_items = query.count()
        total_pages = max(1, (total_items + per_page - 1) // per_page)

        rows = (
            query.order_by(Product.name.asc(), Product.id.asc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        categories = [
            r[0]
            for r in db.session.query(Product.category)
            .filter(Product.organization_id == org.id)
            .distinct()
            .order_by(Product.category.asc())
            .all()
        ]

        return {
            "items": [p.to_dict() for p in rows],
            "categories": categories,
            "meta": {
                "page": page,
                "per_page": per_page,
                "total": total_items,
                "total_items": total_items,
                "total_pages": total_pages,
            },
        }

    @staticmethod
    def list_shop_products(
        actor: User,
        shop_id: int,
        search_query: str | None = None,
        category: str | None = None,
        is_active: bool | None = None,
        low_stock_only: bool = False,
        near_expiry_only: bool = False,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Return paginated shop catalog products with reconciled batch stock totals (API-013)."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=True,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        page, per_page = validate_pagination_params(page, per_page)
        today = get_business_today()

        # Run expiry sweep so sellable_qty never includes past-due stock
        InventoryService.sweep_expired_batches(shop_id=shop.id, ref_date=today)

        query = (
            db.session.query(ShopProduct)
            .join(Product, ShopProduct.product_id == Product.id)
            .filter(
                ShopProduct.shop_id == shop.id,
                Product.organization_id == shop.organization_id,
            )
        )

        if search_query and search_query.strip():
            term = f"%{search_query.strip()}%"
            query = query.filter(
                or_(
                    Product.sku.ilike(term),
                    Product.name.ilike(term),
                    Product.category.ilike(term),
                    Product.description.ilike(term),
                )
            )

        if category and category.strip():
            query = query.filter(func.lower(Product.category) == category.strip().lower())

        if is_active is not None:
            if is_active:
                query = query.filter(
                    ShopProduct.is_active.is_(True),
                    Product.is_active.is_(True),
                )
            else:
                query = query.filter(
                    or_(
                        ShopProduct.is_active.is_(False),
                        Product.is_active.is_(False),
                    )
                )

        all_matching = query.order_by(Product.name.asc(), ShopProduct.id.asc()).all()

        enriched: list[dict[str, Any]] = []
        for sp in all_matching:
            summary = InventoryService.compute_shop_product_stock_summary(
                shop_product=sp,
                ref_date=today,
            )
            if low_stock_only and not summary["is_low_stock"]:
                continue
            if near_expiry_only and summary["near_expiry_batch_count"] <= 0:
                continue
            enriched.append(sp.to_dict(stock_summary=summary))

        total_items = len(enriched)
        total_pages = max(1, (total_items + per_page - 1) // per_page)
        start_idx = (page - 1) * per_page
        page_items = enriched[start_idx : start_idx + per_page]

        categories = [
            r[0]
            for r in db.session.query(Product.category)
            .join(ShopProduct, ShopProduct.product_id == Product.id)
            .filter(ShopProduct.shop_id == shop.id)
            .distinct()
            .order_by(Product.category.asc())
            .all()
        ]

        # Also list organization products not yet enrolled in this shop (for quick enrollment UI)
        enrolled_product_ids = {
            r[0]
            for r in db.session.query(ShopProduct.product_id)
            .filter(ShopProduct.shop_id == shop.id)
            .all()
        }
        unenrolled_org_products = (
            db.session.query(Product)
            .filter(
                Product.organization_id == shop.organization_id,
                Product.is_active.is_(True),
                ~Product.id.in_(enrolled_product_ids) if enrolled_product_ids else True,
            )
            .order_by(Product.name.asc())
            .all()
        )

        return {
            "items": page_items,
            "categories": categories,
            "unenrolled_org_products": [p.to_dict() for p in unenrolled_org_products],
            "meta": {
                "page": page,
                "per_page": per_page,
                "total": total_items,
                "total_items": total_items,
                "total_pages": total_pages,
                "business_date": today.isoformat(),
            },
        }

    @staticmethod
    def get_shop_product_detail(
        actor: User,
        shop_id: int,
        identifier: int,
    ) -> dict[str, Any]:
        """Return detailed ShopProduct view including reconciled stock, FEFO batches, and movements."""
        shop, _ = verify_shop_access(
            user=actor,
            shop_id=shop_id,
            allow_inactive=True,
            required_shop_roles={"SUPER_ADMIN", "SHOP_OWNER", "STAFF"},
        )
        shop_product = CatalogService.resolve_shop_product(shop.id, identifier)
        today = get_business_today()

        InventoryService.sweep_expired_batches(
            shop_id=shop.id,
            product_id=shop_product.product_id,
            ref_date=today,
        )

        batches = (
            db.session.query(InventoryBatch)
            .filter(
                InventoryBatch.shop_id == shop.id,
                InventoryBatch.product_id == shop_product.product_id,
            )
            .order_by(
                case((InventoryBatch.expiry_date.is_(None), 1), else_=0).asc(),
                InventoryBatch.expiry_date.asc(),
                InventoryBatch.received_at.asc(),
                InventoryBatch.id.asc(),
            )
            .all()
        )

        summary = InventoryService.compute_shop_product_stock_summary(
            shop_product=shop_product,
            ref_date=today,
            batches=batches,
        )
        data = shop_product.to_dict(stock_summary=summary)
        data["batches"] = [
            b.to_dict(ref_date=today, near_expiry_days=shop_product.near_expiry_days)
            for b in batches
        ]

        movements = (
            db.session.query(StockMovement)
            .filter(
                StockMovement.shop_id == shop.id,
                StockMovement.product_id == shop_product.product_id,
            )
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
            .limit(50)
            .all()
        )
        data["recent_movements"] = [m.to_dict() for m in movements]
        return data
