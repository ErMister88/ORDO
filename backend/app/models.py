"""Pydantic request/response models."""
from pydantic import BaseModel, Field
from typing import Annotated, Any, List, Optional, Literal
from datetime import datetime

from .core import Role


MoneyValue = Annotated[float, Field(ge=0, allow_inf_nan=False)]
PositiveMoneyValue = Annotated[float, Field(gt=0, allow_inf_nan=False)]
PositiveQuantity = Annotated[float, Field(gt=0, allow_inf_nan=False)]
PercentValue = Annotated[int, Field(ge=0, le=100)]


class PublicUser(BaseModel):
    id: str
    email: str
    name: str
    role: Role
    companyId: Optional[str] = None
    salesRepId: Optional[str] = None
    must_change_password: bool = False


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PublicUser


class OfferItemIn(BaseModel):
    productId: str
    qty: PositiveQuantity
    price: PositiveMoneyValue
    approvalId: Optional[str] = None


class OfferCreate(BaseModel):
    companyId: str
    items: List[OfferItemIn] = Field(max_length=200)
    termMonths: int = 48
    reason: Optional[str] = ""
    billingAddressId: Optional[str] = None
    deliveryAddressId: Optional[str] = None


class OrderItemIn(BaseModel):
    productId: str
    qty: PositiveQuantity


class OrderCreate(BaseModel):
    companyId: str
    items: List[OrderItemIn] = Field(max_length=200)
    paymentMethod: Literal["bank_transfer", "cash", "card", "other"] = "bank_transfer"
    createInvoice: bool = False
    paymentTermDays: Optional[Annotated[int, Field(ge=0, le=3650)]] = None
    billingAddressId: Optional[str] = None
    deliveryAddressId: Optional[str] = None


class DecisionIn(BaseModel):
    note: Optional[str] = ""


class DiscountTier(BaseModel):
    minQty: PositiveQuantity
    price: PositiveMoneyValue


class ProductIn(BaseModel):
    sku: str = Field(default="", max_length=100)
    ean: str = Field(default="", max_length=50)
    brand: str = Field(default="", max_length=200)
    brandId: Optional[str] = None
    name: str = Field(max_length=200)
    categoryId: Optional[str] = None
    collectionIds: List[str] = Field(default_factory=list)
    unit: str = "piece"
    packagingUnit: str = Field(default="", max_length=100)
    packageQuantity: Optional[PositiveQuantity] = None
    contentAmount: Optional[PositiveQuantity] = None
    contentUnit: str = ""
    minimumOrderQuantity: Optional[PositiveQuantity] = None
    b2bAvailable: bool = True
    b2cAvailable: bool = False
    directPurchaseAllowed: bool = True
    financingRequestAllowed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    standardPrice: PositiveMoneyValue
    salesFloor: PositiveMoneyValue
    absoluteFloor: PositiveMoneyValue
    cost: MoneyValue
    description: str = Field(default="", max_length=10_000)
    imageUrl: str = Field(default="", max_length=2_000)
    discountTiers: List[DiscountTier] = Field(default_factory=list, max_length=100)
    b2cTiers: List[DiscountTier] = Field(default_factory=list, max_length=100)
    taxRate: PercentValue
    stock: Optional[float] = None
    b2cPrice: Optional[PositiveMoneyValue] = None
    active: bool = True


class CustomerPriceIn(BaseModel):
    companyId: str
    productId: str
    price: PositiveMoneyValue
    deliveryTerms: str = ""
    transportModel: str = ""
    paymentTermDays: Optional[Annotated[int, Field(ge=0, le=3650)]] = None
    minimumQuantity: Optional[PositiveQuantity] = None
    validFrom: Optional[datetime] = None
    validUntil: Optional[datetime] = None
    internalNote: str = ""
    sourceReference: str = ""


class B2BPromotionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    productId: str
    companyId: Optional[str] = None
    price: PositiveMoneyValue
    startsAt: datetime
    endsAt: datetime


class PricingQuoteIn(BaseModel):
    companyId: str
    items: List[OrderItemIn]


class OrderStatusIn(BaseModel):
    status: str


class ActiveIn(BaseModel):
    active: bool


class StockIn(BaseModel):
    stock: Optional[float] = None


class ShopSettingsIn(BaseModel):
    freeShippingThreshold: MoneyValue
    shippingFee: MoneyValue
    newsletterDiscountPercent: PercentValue
    newsletterDiscountEnabled: bool = True
    subscriptionDiscountPercent: Optional[PercentValue] = None


class ShopCollectionIn(BaseModel):
    name: str
    description: str = ""
    imageUrl: str = ""
    sortOrder: int = 0
    active: bool = True


class ShopCustomerIn(BaseModel):
    name: str = Field(max_length=200)
    email: str = Field(max_length=320)
    phone: str = Field(default="", max_length=80)
    street: str = Field(default="", max_length=300)
    zip: str = Field(default="", max_length=30)
    city: str = Field(default="", max_length=200)


class ShopItemIn(BaseModel):
    productId: str
    qty: PositiveQuantity


class ShopQuoteIn(BaseModel):
    items: List[ShopItemIn] = Field(max_length=200)
    promoCode: Optional[str] = Field(default=None, max_length=100)
    subscription: bool = False


class ShopOrderIn(BaseModel):
    items: List[ShopItemIn] = Field(max_length=200)
    customer: ShopCustomerIn
    promoCode: Optional[str] = None
    subscription: bool = False


class NewsletterIn(BaseModel):
    email: str = Field(max_length=320)
    name: Optional[str] = Field(default="", max_length=200)
    baseUrl: Optional[str] = Field(default=None, max_length=2_000)


class ValidateCodeIn(BaseModel):
    code: str


class PushBroadcastIn(BaseModel):
    title: str
    message: str
    actionUrl: Optional[str] = None


class ShopStatusIn(BaseModel):
    status: str
    trackingNumber: Optional[str] = None


class ShopAddressIn(BaseModel):
    name: str = ""
    phone: str = ""
    street: str = ""
    zip: str = ""
    city: str = ""


class MachineIn(BaseModel):
    productId: Optional[str] = None
    name: str
    description: str = ""
    imageUrl: str = ""
    price: PositiveMoneyValue  # Configured gross B2C purchase price.
    taxRate: PercentValue
    active: bool = True


class MachineRequestIn(BaseModel):
    machineId: str
    type: Literal["kauf", "finanzierung", "leasing", "bereitstellung"]
    termMonths: Optional[int] = 48
    productId: Optional[str] = None
    expectedCoffeeKgMonth: Optional[PositiveQuantity] = None
    companyName: str = ""
    contactName: str = ""
    contactEmail: str = ""
    contactPhone: str = ""
    message: str = ""


class EquipmentFinancingRequestIn(BaseModel):
    productId: str
    name: str = Field(max_length=200)
    email: str = Field(max_length=320)
    phone: str = Field(default="", max_length=80)
    message: str = Field(default="", max_length=5_000)


class MachineTermsIn(BaseModel):
    status: Optional[str] = None
    downPayment: Optional[MoneyValue] = None
    monthlyRate: Optional[MoneyValue] = None
    finalPayment: Optional[MoneyValue] = None
    termMonths: Optional[int] = None
    minCoffeeKgMonth: Optional[PositiveQuantity] = None
    productId: Optional[str] = None
    coffeePricePerKg: Optional[PositiveMoneyValue] = None
    note: str = ""


class MachineRespondIn(BaseModel):
    action: str  # "decline" | "question"
    message: str = ""


class ShopRegisterIn(BaseModel):
    name: str
    email: str
    password: str


class ShopLoginIn(BaseModel):
    email: str
    password: str


class NewCompanyIn(BaseModel):
    name: str
    city: str = ""
    email: str = ""
    phone: str = ""


class CustomerAddressIn(BaseModel):
    type: Literal["main", "billing", "shipping"]
    label: str = ""
    street: str
    houseNumber: str = ""
    zip: str
    city: str
    country: str = "DE"
    active: bool = True


class CustomerContactIn(BaseModel):
    firstName: str
    lastName: str
    title: str = ""
    phone: str = ""
    mobile: str = ""
    email: str
    active: bool = True


class CompanyUpdateIn(BaseModel):
    name: str
    city: str = ""
    email: str = ""
    phone: str = ""
    vatId: str = ""
    taxNumber: str = ""
    assignedSalesRepId: Optional[str] = None
    orderCycleDays: int = 30
    active: bool = True
    customerTypeId: Optional[str] = None
    customerTagIds: List[str] = Field(default_factory=list)
    status: Optional[Literal["Lead", "Interessent", "Neukunde", "Aktiv", "Inaktiv", "Gesperrt"]] = None


class CompanyCreateIn(BaseModel):
    name: str
    city: str = ""
    email: str = ""
    phone: str = ""
    vatId: str = ""
    taxNumber: str = ""
    status: Literal["Lead", "Interessent", "Neukunde", "Aktiv", "Inaktiv", "Gesperrt"] = "Lead"
    assignedSalesRepId: Optional[str] = None
    orderCycleDays: Annotated[int, Field(ge=1, le=3650)] = 30
    customerTypeId: Optional[str] = None
    customerTagIds: List[str] = Field(default_factory=list)
    primaryAddress: Optional[CustomerAddressIn] = None
    primaryContact: Optional[CustomerContactIn] = None


class PriceApprovalDecisionIn(BaseModel):
    note: str = ""
    persistence: Literal["one_time", "customer_price"] = "customer_price"


class CompanyAssignmentIn(BaseModel):
    assignedSalesRepId: Optional[str] = None


class CompanyStatusIn(BaseModel):
    status: Literal["Lead", "Interessent", "Neukunde", "Aktiv", "Inaktiv", "Gesperrt"]


class CustomerActivityIn(BaseModel):
    type: Literal["call", "visit", "note", "service"]
    title: str
    note: str = ""
    internal: bool = True
    occurredAt: Optional[datetime] = None


class CustomerTaskIn(BaseModel):
    title: str
    note: str = ""
    dueAt: datetime
    assignedUserId: Optional[str] = None


class CustomerTaskUpdateIn(BaseModel):
    status: Literal["open", "completed", "cancelled"]


class ProductCategoryIn(BaseModel):
    name: str
    description: str = ""
    sortOrder: int = 0
    active: bool = True


class BusinessClassificationIn(BaseModel):
    name: str
    description: str = ""
    sortOrder: int = 0
    active: bool = True


class InvoiceStatusIn(BaseModel):
    status: Literal["Entwurf", "Offen", "Teilweise bezahlt", "Bezahlt", "Überfällig", "Storniert"]


class PaymentRecordIn(BaseModel):
    amount: PositiveMoneyValue
    method: Literal["bank_transfer", "cash", "card", "other"]
    paidAt: Optional[datetime] = None
    reference: str = ""


class CreateUserIn(BaseModel):
    name: str
    email: str
    role: Literal["sales", "customer"]
    companyId: Optional[str] = None
    newCompany: Optional[NewCompanyIn] = None


class ForgotPwIn(BaseModel):
    email: str


class ResetPwIn(BaseModel):
    email: str
    code: str
    newPassword: str


class ChangePwIn(BaseModel):
    currentPassword: str
    newPassword: str


class AcceptOfferIn(BaseModel):
    note: Optional[str] = ""


class SubscriptionIn(BaseModel):
    companyId: str
    items: List[OrderItemIn]
    intervalDays: int = 28


class CollectiveInvoiceIn(BaseModel):
    companyId: str
    year: int
    month: int
