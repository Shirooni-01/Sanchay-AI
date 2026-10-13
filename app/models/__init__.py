"""SQLAlchemy declarative models for Phases 1, 2, 3, and 4.

Includes User, AuditLog, Organization, Shop, UserShopAssignment,
Product, ShopProduct, InventoryBatch, StockMovement,
Sale, SaleItem, and SaleReturn.
Future domain models (TransferRequest, Notification, and Phase 8 AI models)
are strictly deferred to their respective approved phases.
"""

from app.models.audit import AuditLog
from app.models.catalog import Product, ShopProduct
from app.models.inventory import InventoryBatch, StockMovement
from app.models.notification import Notification
from app.models.organization import Organization, Shop, UserShopAssignment
from app.models.sales import Sale, SaleItem, SaleReturn
from app.models.transfer import TransferRequest, TransferStatusLog
from app.models.user import User

__all__ = [
    "User",
    "AuditLog",
    "Organization",
    "Shop",
    "UserShopAssignment",
    "Product",
    "ShopProduct",
    "InventoryBatch",
    "StockMovement",
    "Sale",
    "SaleItem",
    "SaleReturn",
    "TransferRequest",
    "TransferStatusLog",
    "Notification",
]
